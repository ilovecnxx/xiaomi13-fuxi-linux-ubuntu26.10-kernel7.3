#include <libmm-glib.h>
#include <libqmi-glib.h>
#include <glib/gstdio.h>

enum Reply { MATCH, OTHER, MISSING, READ_ERROR, BIND_ERROR };
static enum Reply reply;
static QmiSubscriptionType expected;
static GString *events;
static QmiClientWds *active_client;

void qmi_client_wds_bind_subscription(QmiClientWds *client, QmiMessageWdsBindSubscriptionInput *input,
                                      guint timeout, GCancellable *cancel, GAsyncReadyCallback callback, gpointer data)
{
    QmiSubscriptionType sent;
    g_assert_true(client == active_client);
    g_assert_true(qmi_message_wds_bind_subscription_input_get_subscription_id(input, &sent, NULL));
    g_assert_cmpint(sent, ==, expected);
    g_string_append_c(events, 'B');
    callback(G_OBJECT(client), NULL, data);
}

QmiMessageWdsBindSubscriptionOutput *qmi_client_wds_bind_subscription_finish(QmiClientWds *client,
                                                                             GAsyncResult *res, GError **error)
{
    return (QmiMessageWdsBindSubscriptionOutput *)client;
}

gboolean qmi_message_wds_bind_subscription_output_get_result(QmiMessageWdsBindSubscriptionOutput *output, GError **error)
{
    if (reply != BIND_ERROR)
        return TRUE;
    g_set_error_literal(error, QMI_PROTOCOL_ERROR, QMI_PROTOCOL_ERROR_INTERNAL, "bind rejected");
    return FALSE;
}

void qmi_message_wds_bind_subscription_output_unref(QmiMessageWdsBindSubscriptionOutput *output) {}

void qmi_client_wds_get_bind_subscription(QmiClientWds *client, gpointer unused, guint timeout,
                                          GCancellable *cancel, GAsyncReadyCallback callback, gpointer data)
{
    g_assert_true(client == active_client);
    g_string_append_c(events, 'G');
    callback(G_OBJECT(client), NULL, data);
}

QmiMessageWdsGetBindSubscriptionOutput *qmi_client_wds_get_bind_subscription_finish(QmiClientWds *client,
                                                                                    GAsyncResult *res, GError **error)
{
    if (reply != READ_ERROR)
        return (QmiMessageWdsGetBindSubscriptionOutput *)client;
    g_set_error_literal(error, G_IO_ERROR, G_IO_ERROR_TIMED_OUT, "read timed out");
    return NULL;
}

gboolean qmi_message_wds_get_bind_subscription_output_get_result(QmiMessageWdsGetBindSubscriptionOutput *output, GError **error)
{
    return TRUE;
}

gboolean qmi_message_wds_get_bind_subscription_output_get_subscription_id(QmiMessageWdsGetBindSubscriptionOutput *output,
                                                                          QmiSubscriptionType *actual, GError **error)
{
    if (reply == MISSING) {
        g_set_error_literal(error, QMI_CORE_ERROR, QMI_CORE_ERROR_TLV_NOT_FOUND, "missing subscription");
        return FALSE;
    }
    *actual = reply == OTHER ? (expected == QMI_SUBSCRIPTION_TYPE_PRIMARY ?
                                QMI_SUBSCRIPTION_TYPE_SECONDARY : QMI_SUBSCRIPTION_TYPE_PRIMARY) : expected;
    return TRUE;
}

void qmi_message_wds_get_bind_subscription_output_unref(QmiMessageWdsGetBindSubscriptionOutput *output) {}

typedef struct { guint step; } ConnectContext;
#define mm_obj_info(...) ((void)0)
#define MM_PORT_QMI_SUB_BIND_FILE "enabled"

static void connect_context_step(GTask *task) { g_string_append_c(events, 'N'); }
static void complete_connect(GTask *task, gpointer result, GError *error)
{
    g_assert_nonnull(error);
    g_error_free(error);
    g_string_append_c(events, 'E');
}

#include "mm-bind.inc"
#include "qvd-connection.c"

void qmi_client_wds_set_ip_family(QmiClientWds *client, QmiMessageWdsSetIpFamilyInput *input, guint timeout,
                                  GCancellable *cancel, GAsyncReadyCallback callback, gpointer data)
{
    g_assert_true(client == active_client);
    g_string_append_c(events, 'I');
    g_object_unref(data);
}

void qmi_device_release_client(QmiDevice *device, QmiClient *client, QmiDeviceReleaseClientFlags flags,
                               guint timeout, GCancellable *cancel, GAsyncReadyCallback callback, gpointer data)
{
    g_assert_true(client == QMI_CLIENT(active_client));
    g_string_append_c(events, 'R');
}

static void connect_error(QVDConnection *conn, GError *error, gpointer data)
{
    g_assert_nonnull(error);
    g_string_append_c(events, 'E');
}

int main(void)
{
    g_autoptr(GTask) task = g_task_new(NULL, NULL, NULL, NULL);
    ConnectContext ctx = {0};
    g_task_set_task_data(task, &ctx, NULL);
    active_client = g_object_new(QMI_TYPE_CLIENT_WDS, NULL);
    events = g_string_new(NULL);
    g_assert_false(bind_primary_subscription(active_client, task));
    g_assert_cmpstr(events->str, ==, "");
    g_assert_true(g_file_set_contents(MM_PORT_QMI_SUB_BIND_FILE, "", 0, NULL));

    for (reply = MATCH; reply <= BIND_ERROR; reply++) {
        expected = QMI_SUBSCRIPTION_TYPE_PRIMARY;
        ctx.step = 0;
        g_string_truncate(events, 0);
        g_assert_true(bind_primary_subscription(active_client, task));
        g_assert_cmpstr(events->str, ==, reply == MATCH ? "BGN" : reply == BIND_ERROR ? "BE" : "BGE");
        g_assert_cmpuint(ctx.step, ==, reply == MATCH);

        expected = QMI_SUBSCRIPTION_TYPE_SECONDARY;
        QVDConnection *conn = qvd_connection_new_wds(expected, "IMS", MM_BEARER_IP_FAMILY_IPV6, 0xffff);
        conn->qmi = g_object_new(QMI_TYPE_DEVICE, NULL);
        conn->wds = g_object_ref(active_client);
        g_signal_connect(conn, "qvd-connect-error", G_CALLBACK(connect_error), NULL);
        g_string_truncate(events, 0);
        qvd_connection_connect(conn);
        g_assert_cmpstr(events->str, ==, reply == MATCH ? "BGI" : reply == BIND_ERROR ? "BRE" : "BGRE");
        g_assert_true((conn->wds == active_client) == (reply == MATCH));
        g_object_unref(conn);
    }
    g_object_unref(active_client);
    g_string_free(events, TRUE);
    return 0;
}
