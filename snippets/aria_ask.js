var host = global("ARIA_HOST");
var token = global("ARIA_TOKEN");
var text = local("voice_result");

var xhr = new XMLHttpRequest();
xhr.open("POST", host + "/ask/start", false);
xhr.setRequestHeader("Content-Type", "application/json");
xhr.setRequestHeader("Authorization", "Bearer " + token);
xhr.send(JSON.stringify({text: text}));

if (xhr.status != 200) {
  setLocal("ask_success", "0");
  exit();
}

var task_id = JSON.parse(xhr.responseText).task_id;

for (var i = 0; i < 60; i++) {
  var poll = new XMLHttpRequest();
  poll.open("GET", host + "/ask/result/" + task_id, false);
  poll.setRequestHeader("Authorization", "Bearer " + token);
  poll.responseType = "arraybuffer";
  poll.send();

  if (poll.status == 200) {
    writeFile("ARIA/response.wav", poll.response, false);
    setLocal("ask_success", "1");
    exit();
  }

  java.lang.Thread.sleep(3000);
}

setLocal("ask_success", "0");
