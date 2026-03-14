// === ARIA Ask — Failover + Long Timeout ===
//
// Tasker globals (set once in VARIABLES tab):
//   ARIA_HOST_PRIMARY  = http://100.107.139.121:8450  (beardos)
//   ARIA_HOST_FALLBACK = http://100.70.66.104:8450    (slappy)
//   ARIA_TOKEN         = <your-auth-token>
//
// Input:  %voice_result (from Variable Set before this action)
// Output: %ask_success, %active_host, %poll_task_id

var primary = global("ARIA_HOST_PRIMARY");
var fallback = global("ARIA_HOST_FALLBACK");
var token = global("ARIA_TOKEN");
var text = local("voice_result");

var MAX_POLL_TIME = 3600000;

var POLL_SCHEDULE = [
  [60000,   3000],
  [300000,  10000],
  [3600000, 30000]
];

function getPollInterval(elapsed) {
  for (var i = 0; i < POLL_SCHEDULE.length; i++) {
    if (elapsed < POLL_SCHEDULE[i][0]) return POLL_SCHEDULE[i][1];
  }
  return 30000;
}

function postStart(host) {
  try {
    var xhr = new XMLHttpRequest();
    xhr.open("POST", host + "/ask/start", false);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.setRequestHeader("Authorization", "Bearer " + token);
    xhr.send(JSON.stringify({text: text}));
    if (xhr.status == 200) {
      return JSON.parse(xhr.responseText).task_id;
    }
  } catch (e) {}
  return null;
}

function checkStatus(host, taskId) {
  try {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", host + "/ask/status/" + taskId, false);
    xhr.setRequestHeader("Authorization", "Bearer " + token);
    xhr.send();
    if (xhr.status == 200) return "done";
    if (xhr.status == 202) return "processing";
    return "error";
  } catch (e) {
    return "fail";
  }
}

// Output variables — no var, so Tasker exports them as locals
ask_success = "0";
active_host = "";
poll_task_id = "";

flash("ARIA: connecting...");

// --- Failover ---
var task_id = postStart(primary);
var host = null;

if (task_id) {
  host = primary;
} else {
  say("Beardos is offline. Running from slappy.");
  task_id = postStart(fallback);
  if (task_id) {
    host = fallback;
  }
}

if (!task_id) {
  say("Both servers are offline. Your request has been queued.");
} else {
  flash("ARIA: processing...");
  var start = Date.now();
  var notified = false;
  var done = false;

  while (!done && (Date.now() - start < MAX_POLL_TIME)) {
    var elapsed = Date.now() - start;

    // Use Tasker's built-in wait() — NOT java.lang.Thread.sleep()
    wait(getPollInterval(elapsed));

    if (!notified && elapsed > 30000) {
      flash("ARIA is still working...");
      notified = true;
    }

    try {
      var status = checkStatus(host, task_id);

      if (status == "done") {
        ask_success = "1";
        active_host = host;
        poll_task_id = task_id;
        // Full URL and auth header ready for Tasker HTTP Request
        audio_url = host + "/ask/result/" + task_id;
        auth_header = "Bearer " + token;
        done = true;
      } else if (status == "error" || status == "fail") {
        say("Something went wrong. Please try again.");
        done = true;
      }
    } catch (e) {
      say("Lost connection to the server.");
      done = true;
    }
  }

  if (!done) {
    say("That request timed out.");
  }
}

// Belt-and-suspenders: also set via setLocal
setLocal("ask_success", ask_success);
setLocal("active_host", active_host);
setLocal("poll_task_id", poll_task_id);
setLocal("audio_url", audio_url);
setLocal("auth_header", auth_header);
