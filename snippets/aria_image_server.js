// === ARIA Image Receiver — Tasker Setup ===
//
// No JavaScriptlet needed — this is pure Tasker actions.
// This file documents the exact setup for reference.
//
// === HTTP REQUEST PROFILE ===
//
// PROFILES tab → + → Event → Net → HTTP Request
//   Port: 8451
//   Method: POST
//   Path: /image
//   Quick Response: (leave blank — task sends response via HTTP Response action)
//   Timeout: 30
//   Link to task: "ARIA Image Display"
//
// === ARIA IMAGE DISPLAY TASK (3 steps) ===
//
// Step 1: File → Copy File
//         From: %http_request_multipart_values(1)
//         To: ARIA/latest_image.png
//
// Step 2: Net → HTTP Response
//         Request ID: %http_request_id
//         Response Code: 200
//         Body: OK
//
// Step 3: Input → Text/Image Dialog
//         Title: %http_request_multipart_values(2)
//         Image: ARIA/latest_image.png
//         Button 1: OK
//         Close After: 300
//
// === VARIABLES ===
//
// Tasker exposes these from the HTTP Request event:
//   %http_request_multipart_names()   — field names array (image, caption)
//   %http_request_multipart_values()  — field values array (cache file path, caption text)
//   %http_request_id                  — request ID for HTTP Response action
//
// The HTTP Response MUST come before the Text/Image Dialog,
// otherwise the dialog blocks the task and the sender times out.
