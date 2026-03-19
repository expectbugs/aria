// ARIA Location Reporter — Tasker JavaScriptlet
//
// Setup:
// 1. Create task "ARIA Location" with these steps:
//    Step 1: Get Location  v2
//            Source: Any (GPS then Net)
//            Timeout: 30
//    Step 2: JavaScriptlet — paste this script
//            Timeout: 30
//
// 2. Create profile: Time → every 5 minutes
//    Link to "ARIA Location" task
//
// Tasker variables available after Get Location v2:
//   %gl_latitude, %gl_longitude, %gl_coordinates_accuracy
//   %gl_speed (m/s, may be 0)
//   %BATT (battery level, always available)

var host = global("ARIA_HOST_PRIMARY");
var status = global("ARIA_STATUS");
if (status === "fallback") host = global("ARIA_HOST_FALLBACK");
if (status === "offline") exit();

var lat = local("gl_latitude");
var lon = local("gl_longitude");

if (!lat || !lon || lat === "%gl_latitude") {
    // Location not available, skip silently
} else {
    var payload = {
        "lat": parseFloat(lat),
        "lon": parseFloat(lon)
    };

    var acc = local("gl_coordinates_accuracy");
    if (acc && acc !== "%gl_coordinates_accuracy") payload.accuracy = parseFloat(acc);

    var spd = local("gl_speed");
    if (spd && spd !== "%gl_speed") payload.speed = parseFloat(spd);

    var batt = global("BATT");
    if (batt && batt !== "%BATT") payload.battery = parseInt(batt);

    try {
        var xhr = new XMLHttpRequest();
        xhr.open("POST", host + "/location", false);
        xhr.setRequestHeader("Authorization", "Bearer " + global("ARIA_TOKEN"));
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.send(JSON.stringify(payload));
    } catch (e) {
        // Fail silently — location is non-critical
    }
}
