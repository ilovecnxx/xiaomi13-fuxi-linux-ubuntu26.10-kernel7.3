/* Compile the production callback with decoded-report fixtures. */
#include <glib.h>
#include "mm-fuxi-loc.h"

#define DEFAULT_LOC_SESSION_ID 0x10
#define QMI_LOC_SESSION_STATUS_SUCCESS 0
#define QMI_LOC_TECHNOLOGY_USED_SATELLITE 1
#define MM_IFACE_MODEM_LOCATION(self) (self)
#define mm_obj_dbg(...) ((void) 0)
typedef int QmiLocSessionStatus;
typedef int QmiLocTechnologyUsed;
typedef int QmiClientLoc;
typedef int MMSharedQmi;
typedef struct {
    guint present;
    guint8 session;
    QmiLocSessionStatus status;
    QmiLocTechnologyUsed technology;
    guint64 timestamp;
    gdouble latitude, longitude;
    gfloat altitude;
} QmiIndicationLocPositionReportOutput;

#define GETTER(name, field, type, bit) \
static gboolean qmi_indication_loc_position_report_output_get_##name ( \
    QmiIndicationLocPositionReportOutput *report, type *value, GError **error) \
{ \
    if (!(report->present & (1 << bit))) return FALSE; \
    *value = report->field; \
    return TRUE; \
}
GETTER (session_id, session, guint8, 0)
GETTER (session_status, status, QmiLocSessionStatus, 1)
GETTER (technology_used, technology, QmiLocTechnologyUsed, 2)
GETTER (utc_timestamp, timestamp, guint64, 3)
GETTER (latitude, latitude, gdouble, 4)
GETTER (longitude, longitude, gdouble, 5)
GETTER (altitude_from_sealevel, altitude, gfloat, 6)

static GVariant *published;
static void
mm_iface_modem_location_gps_raw_update (MMSharedQmi *self, GVariant *dictionary)
{
    g_assert_null (published);
    published = g_variant_ref (dictionary);
}

#include "loc-callback.inc"

static void
assert_no_position (QmiIndicationLocPositionReportOutput report)
{
    loc_position_indication_cb (NULL, &report, NULL);
    g_assert_null (published);
}

int main (void)
{
    QmiIndicationLocPositionReportOutput valid = {
        .present = 127, .session = 0x10, .status = 0, .technology = 1,
        .timestamp = 1001, .latitude = 0.0, .longitude = 0.0, .altitude = 12.5,
    };
    const gchar *utc;
    gdouble value;

    loc_position_indication_cb (NULL, &valid, NULL);
    g_assert_nonnull (published);
    g_assert_true (g_variant_lookup (published, "utc-time", "&s", &utc));
    g_assert_cmpstr (utc, ==, "000001.001");
    g_assert_true (g_variant_lookup (published, "latitude", "d", &value));
    g_assert_cmpfloat (value, ==, 0.0);
    g_assert_true (g_variant_lookup (published, "altitude", "d", &value));
    g_assert_cmpfloat (value, ==, 12.5);
    g_clear_pointer (&published, g_variant_unref);

    for (guint bit = 0; bit < 6; bit++) {
        QmiIndicationLocPositionReportOutput report = valid;
        report.present &= ~(1 << bit);
        assert_no_position (report);
    }
    for (guint status = 1; status < 8; status++) {
        QmiIndicationLocPositionReportOutput report = valid;
        report.status = status;
        assert_no_position (report);
    }
    valid.session++;
    assert_no_position (valid);
    valid.session--;
    valid.technology = 2;
    assert_no_position (valid);
    valid.technology = 1;

    const gdouble invalid_lat[] = {NAN, INFINITY, -INFINITY, -90.001, 90.001};
    const gdouble invalid_lon[] = {NAN, INFINITY, -INFINITY, -180.001, 180.001};
    for (guint i = 0; i < G_N_ELEMENTS (invalid_lat); i++) {
        g_assert_null (mm_fuxi_loc_raw_dictionary (1001, invalid_lat[i], 0, NAN));
        g_assert_null (mm_fuxi_loc_raw_dictionary (1001, 0, invalid_lon[i], NAN));
    }
    g_assert_null (mm_fuxi_loc_raw_dictionary (0, 0, 0, NAN));
    g_assert_null (mm_fuxi_loc_raw_dictionary (G_MAXUINT64, 0, 0, NAN));
    valid.present &= ~(1 << 6);
    valid.latitude = -90;
    valid.longitude = 180;
    loc_position_indication_cb (NULL, &valid, NULL);
    g_assert_nonnull (published);
    g_assert_false (g_variant_lookup (published, "altitude", "d", &value));
    g_clear_pointer (&published, g_variant_unref);
    return 0;
}
