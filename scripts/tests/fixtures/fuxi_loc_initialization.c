#include <gio/gio.h>
#include <stdarg.h>

typedef GObject QmiClientLoc;
typedef GObject MMIfaceModemLocation;
typedef GObject MMPortQmi;
typedef GObject MMBroadbandModemQmi;
typedef struct { guint service_index; } InitializationStartedContext;
typedef struct { gboolean success; gint error_code; } QmiMessageLocSetEngineLockOutput;

#define QMI_PROTOCOL_ERROR (g_quark_from_static_string ("qmi-protocol"))
#define QMI_PROTOCOL_ERROR_NO_PERMISSION 1
#define QMI_SERVICE_LOC 16
static const guint qmi_services[] = { 1, QMI_SERVICE_LOC };
static QmiMessageLocSetEngineLockOutput response;
static gboolean transport_failure, allocation_failure, fuxi;
static guint warnings, result_reads, output_releases, capabilities_loaded, advanced;

static void warn_message (gpointer object, const gchar *format, ...)
{
    va_list args;
    va_start (args, format);
    g_autofree gchar *message = g_strdup_vprintf (format, args);
    va_end (args);
    g_assert_nonnull (message);
    warnings++;
}
#define mm_obj_warn warn_message
#define mm_obj_dbg(...) ((void) 0)

static void qmi_message_loc_set_engine_lock_output_unref (QmiMessageLocSetEngineLockOutput *output)
{
    g_assert_true (output == &response);
    output_releases++;
}
G_DEFINE_AUTOPTR_CLEANUP_FUNC (QmiMessageLocSetEngineLockOutput, qmi_message_loc_set_engine_lock_output_unref)

static QmiMessageLocSetEngineLockOutput *
qmi_client_loc_set_engine_lock_finish (QmiClientLoc *client, GAsyncResult *res, GError **error)
{
    if (transport_failure) {
        g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_TIMED_OUT, "request timed out");
        return NULL;
    }
    return &response;
}

static gboolean qmi_message_loc_set_engine_lock_output_get_result (QmiMessageLocSetEngineLockOutput *output, GError **error)
{
    g_assert_nonnull (output);
    g_assert_null (*error);
    result_reads++;
    if (!output->success)
        g_set_error_literal (error, QMI_PROTOCOL_ERROR, output->error_code, "protocol error");
    return output->success;
}

static void location_load_capabilities (MMIfaceModemLocation *self, GTask *task)
{
    g_assert_true (self == g_task_get_source_object (task));
    capabilities_loaded++;
}

static gboolean mm_port_qmi_allocate_client_finish (MMPortQmi *port, GAsyncResult *res, GError **error)
{
    if (allocation_failure)
        g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED, "service not supported");
    return !allocation_failure;
}

static gboolean identity_exists (const gchar *path, GFileTest test)
{
    g_assert_cmpstr (path, ==, "/etc/fuxi/mm-loc-identity");
    g_assert_cmpint (test, ==, G_FILE_TEST_EXISTS);
    return fuxi;
}
#define g_file_test identity_exists

static void allocate_next_client (GTask *task) { advanced++; }

#include "loc-initialization.inc"

static void test_unlock (gconstpointer data)
{
    gint scenario = GPOINTER_TO_INT (data);
    g_autoptr(GObject) modem = g_object_new (G_TYPE_OBJECT, NULL);
    g_autoptr(GTask) task = g_task_new (modem, NULL, NULL, NULL);

    transport_failure = scenario == 0;
    response.success = scenario == 1;
    response.error_code = scenario == 2 ? QMI_PROTOCOL_ERROR_NO_PERMISSION : 2;
    warnings = result_reads = output_releases = capabilities_loaded = 0;
    loc_unlock_location_engine_ready (modem, NULL, task);
    g_assert_cmpuint (capabilities_loaded, ==, 1);
    g_assert_cmpuint (result_reads, ==, transport_failure ? 0 : 1);
    g_assert_cmpuint (output_releases, ==, transport_failure ? 0 : 1);
    g_assert_cmpuint (warnings, ==, scenario == 0 || scenario == 3 ? 1 : 0);
}

static void test_allocation (void)
{
    g_autoptr(GObject) modem = g_object_new (G_TYPE_OBJECT, NULL);
    g_autoptr(GTask) task = g_task_new (modem, NULL, NULL, NULL);
    InitializationStartedContext ctx;

    g_task_set_task_data (task, &ctx, NULL);
    for (guint index = 0; index <= G_N_ELEMENTS (qmi_services); index++) {
        for (guint failed = 0; failed < 2; failed++) {
            for (guint marker = 0; marker < 2; marker++) {
                ctx.service_index = index;
                allocation_failure = failed;
                fuxi = marker;
                warnings = advanced = 0;
                qmi_port_allocate_client_ready (modem, NULL, task);
                g_assert_cmpuint (ctx.service_index, ==, index + 1);
                g_assert_cmpuint (advanced, ==, 1);
                g_assert_cmpuint (warnings, ==, failed && marker && index == 1 ? 1 : 0);
            }
        }
    }
}

int main (int argc, char **argv)
{
    g_test_init (&argc, &argv, NULL);
    g_test_add_data_func ("/loc/unlock/transport-failure", GINT_TO_POINTER (0), test_unlock);
    g_test_add_data_func ("/loc/unlock/success", GINT_TO_POINTER (1), test_unlock);
    g_test_add_data_func ("/loc/unlock/no-permission", GINT_TO_POINTER (2), test_unlock);
    g_test_add_data_func ("/loc/unlock/protocol-failure", GINT_TO_POINTER (3), test_unlock);
    g_test_add_func ("/loc/allocation/diagnostic-and-progress", test_allocation);
    return g_test_run ();
}
