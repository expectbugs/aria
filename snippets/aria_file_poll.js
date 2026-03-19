var token = global("ARIA_TOKEN");
var host = global("ARIA_HOST_PRIMARY");
var status = global("ARIA_STATUS");
if (status === "fallback") host = global("ARIA_HOST_FALLBACK");

var data = local("http_data");
var resp = JSON.parse(data);
var taskId = resp.task_id;

ask_success = "0";
audio_url = "";

var pollUrl = host + "/ask/status/" + taskId;
var maxWait = 600;
var elapsed = 0;
var done = false;

while (!done && elapsed < maxWait) {
    var interval;
    if (elapsed < 60) interval = 3;
    else if (elapsed < 300) interval = 10;
    else interval = 30;

    wait(interval * 1000);
    elapsed += interval;

    var poll = new XMLHttpRequest();
    poll.open("GET", pollUrl, false);
    poll.setRequestHeader("Authorization", "Bearer " + token);
    poll.send();

    if (poll.status === 200) {
        done = true;
        audio_url = host + "/ask/result/" + taskId;
        ask_success = "1";
    } else if (poll.status !== 202) {
        done = true;
        flash("ARIA file request failed: " + poll.status);
    }
}

if (!done) {
    flash("ARIA: Request timed out");
}

setLocal("ask_success", ask_success);
setLocal("audio_url", audio_url);
