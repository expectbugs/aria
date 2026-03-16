// === ARIA Image Server — Tasker HTTP Server Handler ===
//
// Runs when Tasker HTTP Server receives a POST to /image.
// Saves the uploaded image and sets variables for the
// Display Image action that follows this JavaScriptlet step.
//
// === TASKER SETUP ===
//
// 1. Create task "ARIA Image Display" with these steps:
//
//    Step 1: JavaScriptlet
//            Code: (paste this script)
//
//    Step 2: IF %img_ready ~ 1
//
//    Step 3: Alert → Text/Image Dialog
//            Title: %img_caption
//            Image: %img_path
//
//    Step 4: End If
//
// 2. Go to Preferences → HTTP Server
//    - Enable HTTP Server on port 8451
//    - Bind to Tailscale interface only (100.113.243.91)
//    - Add route: POST /image → run task "ARIA Image Display"
//
// === END SETUP ===

var filePath = local("http_request_file");
var caption = local("http_request_param_caption") || "ARIA";

img_ready = "0";
img_path = "";
img_caption = "";

if (!filePath) {
  setLocal("http_response_code", "400");
  setLocal("http_response", "No image received");
} else {
  var destDir = "ARIA/images";
  var timestamp = new Date().getTime();
  var destFile = destDir + "/image_" + timestamp + ".png";

  createDir(destDir, false);
  copyFile(filePath, destFile, false);

  img_ready = "1";
  img_path = destFile;
  img_caption = caption;

  setLocal("http_response_code", "200");
  setLocal("http_response", "OK");
}

setLocal("img_ready", img_ready);
setLocal("img_path", img_path);
setLocal("img_caption", img_caption);