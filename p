diff --git a/integration/test_general_cli.py b/integration/test_general_cli.py
new file mode 100644
index 0000000..9f9d18a
--- /dev/null
+++ b/integration/test_general_cli.py
@@ -0,0 +1,46 @@
+from os import mkdir
+from os.path import join
+
+import pytest_twisted
+
+from twisted.internet.error import ProcessTerminated
+
+import util
+
+# see "conftest.py" for the fixtures (e.g. "magic_folder")
+
+
+@pytest_twisted.inlineCallbacks
+def test_web_port_required(request, reactor, temp_dir, introducer_furl):
+    """
+    'magic-folder run' requires --web-port option
+    """
+
+    # enough of a node-dir for "magic-folder" to run .. maybe we
+    # should create a real one?
+    node_dir = join(temp_dir, "webport")
+    mkdir(node_dir)
+    with open(join(node_dir, "tahoe.cfg"), "w") as f:
+        f.write('')
+    with open(join(node_dir, "node.url"), "w") as f:
+        f.write('http://localhost/')
+
+    # run "magic-folder run" without --web-port which should error
+    proto = util._CollectOutputProtocol()
+    proc = util._magic_folder_runner(
+        proto, reactor, request,
+        [
+            "--node-directory", node_dir,
+            "run",
+        ],
+    )
+    try:
+        yield proto.done
+    except ProcessTerminated as e:
+        assert e.exitCode != 0
+        assert u"Must specify a listening endpoint with --web-port" in proto.output.getvalue()
+        return
+
+    assert False, "Expected an error from magic-folder"
+    print("output: '{}'".format(proto.output.getvalue()))
+
diff --git a/integration/util.py b/integration/util.py
index 25e983c..9f3800b 100644
--- a/integration/util.py
+++ b/integration/util.py
@@ -149,7 +149,7 @@ def _magic_folder_runner(proto, reactor, request, other_args):
     return reactor.spawnProcess(
         proto,
         sys.executable,
-        [sys.executable, "-m", "magic_folder.scripts.magic_folder_cli"] + other_args,
+        [sys.executable, "-m", "magic_folder"] + other_args,
     )
 
 
diff --git a/src/magic_folder/cli.py b/src/magic_folder/cli.py
index 717006e..63641b6 100644
--- a/src/magic_folder/cli.py
+++ b/src/magic_folder/cli.py
@@ -656,11 +656,15 @@ def _format_magic_folder_status(now, magic_data):
 
 class RunOptions(BasedirOptions):
     optParameters = [
-        ("web-port", None, "tcp:9889",
-         "String description of an endpoint on which to run the web interface.",
+        ("web-port", None, None,
+         "String description of an endpoint on which to run the web interface (required).",
         ),
     ]
 
+    def postOptions(self):
+        if self['web-port'] is None:
+            raise usage.UsageError("Must specify a listening endpoint with --web-port")
+
 
 def main(options):
     """
