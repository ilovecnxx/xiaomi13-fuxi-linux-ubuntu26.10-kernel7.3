/* The watcher is compiled unchanged; only its QRTR transport and logging are fake. */
#include <gio/gio.h>

typedef GObject QrtrNode;
typedef GObject QrtrBus;
G_DEFINE_AUTOPTR_CLEANUP_FUNC (QrtrNode, g_object_unref)
typedef guint32 QmiService;
enum { QMI_SERVICE_WDS = 1, QMI_SERVICE_DMS = 2, QMI_SERVICE_NAS = 3 };
#define QRTR_BUS_SIGNAL_NODE_ADDED "node-added"
#define QRTR_BUS_SIGNAL_NODE_REMOVED "node-removed"

typedef GObject MMLogObject;
typedef struct { GTypeInterface parent; gchar *(*build_id) (MMLogObject *); } MMLogObjectInterface;
static GType mm_log_object_get_type (void);
G_DEFINE_INTERFACE (MMLogObject, mm_log_object, G_TYPE_OBJECT)
static void mm_log_object_default_init (MMLogObjectInterface *iface) {}
#define MM_TYPE_LOG_OBJECT (mm_log_object_get_type ())
#define mm_obj_dbg(...) ((void) 0)
#define mm_obj_warn(...) ((void) 0)

typedef struct { GObject parent; } TestBus;
typedef struct { GObjectClass parent; } TestBusClass;
G_DEFINE_TYPE (TestBus, test_bus, G_TYPE_OBJECT)
static void test_bus_init (TestBus *bus) {}
static void test_bus_class_init (TestBusClass *klass)
{
    g_signal_new (QRTR_BUS_SIGNAL_NODE_ADDED, G_TYPE_FROM_CLASS (klass), G_SIGNAL_RUN_FIRST,
                  0, NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_UINT);
    g_signal_new (QRTR_BUS_SIGNAL_NODE_REMOVED, G_TYPE_FROM_CLASS (klass), G_SIGNAL_RUN_FIRST,
                  0, NULL, NULL, NULL, G_TYPE_NONE, 1, G_TYPE_UINT);
}

static GList *bus_nodes;
static GQueue waits = G_QUEUE_INIT;
static GTask *bus_task;

static guint32 qrtr_node_get_id (QrtrNode *node)
{
    return GPOINTER_TO_UINT (g_object_get_data (node, "id"));
}

static QrtrNode *qrtr_bus_get_node (QrtrBus *bus, guint32 id)
{
    for (GList *l = bus_nodes; l; l = l->next)
        if (qrtr_node_get_id (l->data) == id)
            return g_object_ref (l->data);
    return NULL;
}

static GList *qrtr_bus_peek_nodes (QrtrBus *bus) { return bus_nodes; }

static void qrtr_node_wait_for_services (QrtrNode *node, GArray *services,
                                       guint timeout, GCancellable *cancellable,
                                       GAsyncReadyCallback callback, gpointer data)
{
    GTask *task = g_task_new (node, cancellable, callback, data);
    g_assert_cmpuint (services->len, ==, 3);
    g_assert_cmpuint (timeout, ==, 4000);
    /* Permit a success already queued before cancellation to reach the callback. */
    g_task_set_check_cancellable (task, FALSE);
    g_queue_push_tail (&waits, task);
}

static gboolean qrtr_node_wait_for_services_finish (QrtrNode *node, GAsyncResult *res,
                                                  GError **error)
{
    return g_task_propagate_boolean (G_TASK (res), error);
}

static void qrtr_bus_new (guint timeout, GCancellable *cancellable,
                          GAsyncReadyCallback callback, gpointer data)
{
    g_assert_null (bus_task);
    bus_task = g_task_new (NULL, cancellable, callback, data);
}

static QrtrBus *qrtr_bus_new_finish (GAsyncResult *res, GError **error)
{
    return g_task_propagate_pointer (G_TASK (res), error);
}

#include "mm-qrtr-bus-watcher.c"

typedef struct { GCancellable *authp_cancellable; GHashTable *devices; } ManagerPrivate;
typedef struct { GObject parent; ManagerPrivate *priv; } MMBaseManager;
typedef struct { GObjectClass parent; } MMBaseManagerClass;
G_DEFINE_TYPE (MMBaseManager, test_manager, G_TYPE_OBJECT)
static void test_manager_init (MMBaseManager *manager) {}
static void test_manager_class_init (MMBaseManagerClass *klass) {}
typedef GObject MMDevice;
typedef struct {
    MMBaseManager *self;
    GSList *tasks;
    GSList *devices_to_remove;
    GObject *sleep_ctx;
} ManagerCleanupContext;
static guint sleep_completions;
static const gchar *mm_device_get_uid (MMDevice *device)
{
    g_assert_true (G_IS_OBJECT (device));
    return g_object_get_data (device, "uid");
}
static void mm_sleep_context_complete (GObject *context, GError *error)
{
    sleep_completions++;
}
#include "manager-callbacks.inc"

typedef struct { MMQrtrBusWatcher *watcher; QrtrBus *bus; guint added; guint removed; } Fixture;

static void drain (void)
{
    while (g_main_context_iteration (NULL, FALSE)) {}
}

static void added (MMQrtrBusWatcher *watcher, guint id, Fixture *f) { f->added++; }
static void removed (MMQrtrBusWatcher *watcher, guint id, Fixture *f) { f->removed++; }

static QrtrNode *add_node (Fixture *f, guint id)
{
    QrtrNode *node = g_object_new (G_TYPE_OBJECT, NULL);
    g_object_set_data (node, "id", GUINT_TO_POINTER (id));
    bus_nodes = g_list_append (bus_nodes, node);
    g_signal_emit_by_name (f->bus, QRTR_BUS_SIGNAL_NODE_ADDED, id);
    return node;
}

static void remove_node (Fixture *f, QrtrNode *node)
{
    guint id = qrtr_node_get_id (node);
    bus_nodes = g_list_remove (bus_nodes, node);
    /* The library may already have removed the node when this signal arrives. */
    g_signal_emit_by_name (f->bus, QRTR_BUS_SIGNAL_NODE_REMOVED, id);
    g_object_unref (node);
}

static void complete_wait (gboolean success)
{
    GTask *task = g_queue_pop_head (&waits);
    g_assert_nonnull (task);
    g_task_return_boolean (task, success);
    g_object_unref (task);
    drain ();
}

static void complete_bus (Fixture *f)
{
    g_task_return_pointer (bus_task, g_object_ref (f->bus), g_object_unref);
    g_clear_object (&bus_task);
    drain ();
}

static void setup (Fixture *f, gconstpointer data)
{
    f->bus = g_object_new (test_bus_get_type (), NULL);
    f->watcher = mm_qrtr_bus_watcher_new ();
    g_signal_connect (f->watcher, MM_QRTR_BUS_WATCHER_DEVICE_ADDED, G_CALLBACK (added), f);
    g_signal_connect (f->watcher, MM_QRTR_BUS_WATCHER_DEVICE_REMOVED, G_CALLBACK (removed), f);
    mm_qrtr_bus_watcher_start (f->watcher, NULL, NULL);
}

static void teardown (Fixture *f, gconstpointer data)
{
    mm_qrtr_bus_watcher_pause (f->watcher);
    if (bus_task)
        complete_bus (f);
    while (!g_queue_is_empty (&waits))
        complete_wait (FALSE);
    g_list_free_full (bus_nodes, g_object_unref);
    bus_nodes = NULL;
    g_object_unref (f->watcher);
    g_object_unref (f->bus);
}

static void resume_online (Fixture *f, gconstpointer data)
{
    QrtrNode *node = add_node (f, 7);
    complete_bus (f);
    complete_wait (TRUE);
    g_assert_cmpuint (f->added, ==, 1);
    mm_qrtr_bus_watcher_pause (f->watcher);
    g_assert_null (mm_qrtr_bus_watcher_peek_node (f->watcher, 7));
    mm_qrtr_bus_watcher_scan (f->watcher);
    complete_wait (TRUE);
    g_assert_cmpuint (f->added, ==, 2);
    g_assert_true (mm_qrtr_bus_watcher_peek_node (f->watcher, 7) == node);
    mm_qrtr_bus_watcher_scan (f->watcher);
    g_assert_true (g_queue_is_empty (&waits));
}

static void stale_cycle (Fixture *f, gconstpointer data)
{
    gboolean old_success = GPOINTER_TO_INT (data);
    add_node (f, 7);
    complete_bus (f);
    mm_qrtr_bus_watcher_pause (f->watcher);
    mm_qrtr_bus_watcher_scan (f->watcher);
    complete_wait (old_success);
    g_assert_cmpuint (f->added, ==, 0);
    g_assert_nonnull (mm_qrtr_bus_watcher_peek_node (f->watcher, 7));
    complete_wait (TRUE);
    g_assert_cmpuint (f->added, ==, 1);
}

static void removed_pending (Fixture *f, gconstpointer data)
{
    QrtrNode *old = add_node (f, 7);
    complete_bus (f);
    remove_node (f, old);
    g_assert_cmpuint (f->removed, ==, 1);
    g_assert_null (mm_qrtr_bus_watcher_peek_node (f->watcher, 7));
    complete_wait (TRUE);
    g_assert_cmpuint (f->added, ==, 0);
}

static void reused_id (Fixture *f, gconstpointer data)
{
    QrtrNode *old = add_node (f, 7);
    complete_bus (f);
    remove_node (f, old);
    QrtrNode *current = add_node (f, 7);
    complete_wait (FALSE);
    g_assert_true (mm_qrtr_bus_watcher_peek_node (f->watcher, 7) == current);
    complete_wait (TRUE);
    g_assert_cmpuint (f->added, ==, 1);
}

static void startup_paused (Fixture *f, gconstpointer data)
{
    add_node (f, 7);
    mm_qrtr_bus_watcher_pause (f->watcher);
    complete_bus (f);
    g_assert_true (g_queue_is_empty (&waits));
    add_node (f, 8);
    g_assert_true (g_queue_is_empty (&waits));
    mm_qrtr_bus_watcher_scan (f->watcher);
    complete_wait (TRUE);
    complete_wait (TRUE);
    g_assert_cmpuint (f->added, ==, 2);
}

static void resume_before_bus (Fixture *f, gconstpointer data)
{
    add_node (f, 7);
    mm_qrtr_bus_watcher_pause (f->watcher);
    mm_qrtr_bus_watcher_scan (f->watcher);
    complete_bus (f);
    complete_wait (TRUE);
    g_assert_cmpuint (f->added, ==, 1);
}

static void failed_discovery (Fixture *f, gconstpointer data)
{
    add_node (f, 7);
    complete_bus (f);
    mm_qrtr_bus_watcher_scan (f->watcher);
    g_assert_cmpuint (g_queue_get_length (&waits), ==, 1);
    complete_wait (FALSE);
    g_assert_null (mm_qrtr_bus_watcher_peek_node (f->watcher, 7));
    mm_qrtr_bus_watcher_scan (f->watcher);
    complete_wait (TRUE);
    g_assert_cmpuint (f->added, ==, 1);
}

static void authorization (void)
{
    ManagerPrivate priv = { .authp_cancellable = g_cancellable_new () };
    MMBaseManager manager = { .priv = &priv };
    GCancellable *old = g_object_ref (priv.authp_cancellable);
    resume_authorization (&manager);
    g_assert_true (priv.authp_cancellable == old);
    g_cancellable_cancel (old);
    resume_authorization (&manager);
    g_assert_true (priv.authp_cancellable != old);
    g_assert_false (g_cancellable_is_cancelled (priv.authp_cancellable));
    g_assert_true (g_cancellable_is_cancelled (old));
    g_object_unref (old);
    g_object_unref (priv.authp_cancellable);
}

static void cleanup_devices (void)
{
    ManagerPrivate priv = { .devices = g_hash_table_new_full (g_str_hash, g_str_equal,
                                                            NULL, g_object_unref) };
    MMBaseManager *manager = g_object_new (test_manager_get_type (), NULL);
    ManagerCleanupContext *ctx = g_slice_new0 (ManagerCleanupContext);
    GObject *first = g_object_new (G_TYPE_OBJECT, NULL);
    GObject *second = g_object_new (G_TYPE_OBJECT, NULL);
    manager->priv = &priv;
    g_object_set_data (first, "uid", "one");
    g_object_set_data (second, "uid", "two");
    g_hash_table_insert (priv.devices, "one", first);
    g_hash_table_insert (priv.devices, "two", second);
    ctx->self = g_object_ref (manager);
    ctx->sleep_ctx = g_object_new (G_TYPE_OBJECT, NULL);
    ctx->tasks = g_slist_append (NULL, GINT_TO_POINTER (1));
    ctx->tasks = g_slist_append (ctx->tasks, GINT_TO_POINTER (2));
    manager_cleanup_context_maybe_complete (ctx, GINT_TO_POINTER (1), first, TRUE);
    g_assert_cmpuint (g_hash_table_size (priv.devices), ==, 2);
    manager_cleanup_context_maybe_complete (ctx, GINT_TO_POINTER (2), second, TRUE);
    g_assert_cmpuint (g_hash_table_size (priv.devices), ==, 0);
    g_assert_cmpuint (sleep_completions, ==, 1);
    g_hash_table_destroy (priv.devices);
    g_object_unref (manager);
}

int main (int argc, char **argv)
{
    g_test_init (&argc, &argv, NULL);
    g_test_add ("/qrtr/resume-online", Fixture, NULL, setup, resume_online, teardown);
    g_test_add ("/qrtr/stale-success", Fixture, GINT_TO_POINTER (TRUE), setup, stale_cycle, teardown);
    g_test_add ("/qrtr/stale-failure", Fixture, GINT_TO_POINTER (FALSE), setup, stale_cycle, teardown);
    g_test_add ("/qrtr/removed-pending", Fixture, NULL, setup, removed_pending, teardown);
    g_test_add ("/qrtr/reused-id", Fixture, NULL, setup, reused_id, teardown);
    g_test_add ("/qrtr/startup-paused", Fixture, NULL, setup, startup_paused, teardown);
    g_test_add ("/qrtr/resume-before-bus", Fixture, NULL, setup, resume_before_bus, teardown);
    g_test_add ("/qrtr/failed-discovery", Fixture, NULL, setup, failed_discovery, teardown);
    g_test_add_func ("/manager/authorization", authorization);
    g_test_add_func ("/manager/cleanup-devices", cleanup_devices);
    return g_test_run ();
}
