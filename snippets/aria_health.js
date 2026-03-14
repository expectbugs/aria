// === ARIA Health Check ===
//
// Checks both hosts, sets Tasker global ARIA_STATUS.
// Values: "primary" | "fallback" | "offline"
//
// Tasker globals (input):
//   ARIA_HOST_PRIMARY  = http://100.107.139.121:8450  (beardos)
//   ARIA_HOST_FALLBACK = http://100.70.66.104:8450    (slappy)

var primary = global("ARIA_HOST_PRIMARY");
var fallback = global("ARIA_HOST_FALLBACK");

function checkHealth(host) {
  try {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", host + "/health", false);
    xhr.send();
    if (xhr.status == 200) return true;
  } catch (e) {}
  return false;
}

if (checkHealth(primary)) {
  setGlobal("ARIA_STATUS", "primary");
} else if (checkHealth(fallback)) {
  setGlobal("ARIA_STATUS", "fallback");
} else {
  setGlobal("ARIA_STATUS", "offline");
}
