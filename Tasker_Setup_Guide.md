# ARIA Tasker Setup Guide
## Android Phone Configuration

---

### Prerequisites

- Tasker installed ($4.99, Google Play, by Joao Dias)
- Tailscale installed and connected on your phone
- Phone: moto-g-play-xt2413v (100.67.150.102)
- ARIA daemon: slappy (100.70.66.104:8450)
- Auth token: `cGg2JEiHdtWx0imfIauc1Yas_BLVWnMeUrcublkEseo`

---

### Step 1: Create Global Variables

Go to Tasker > Variables tab, create:

| Variable | Value |
|----------|-------|
| `%ARIA_HOST` | `http://100.70.66.104:8450` |
| `%ARIA_TOKEN` | `cGg2JEiHdtWx0imfIauc1Yas_BLVWnMeUrcublkEseo` |
| `%ARIA_ONLINE` | `0` |

---

### Step 2: Create Task — "ARIA Ask"

This is the main task that sends a voice command to ARIA.

1. **Get Voice** (action: Input > Get Voice)
   - Title: "ARIA"
   - Language Model: Free form
   - Store result in: `%voice_input`

2. **If** `%voice_input` is set

3. **HTTP Request** (action: Net > HTTP Request)
   - Method: POST
   - URL: `%ARIA_HOST/ask`
   - Headers:
     ```
     Content-Type: application/json
     Authorization: Bearer %ARIA_TOKEN
     ```
   - Body: `{"text": "%voice_input"}`
   - Timeout: 180 seconds
   - Store response in: `%http_data`
   - Store response code in: `%http_code`

4. **If** `%http_code` equals `200`
   - **Variable Search Replace**
     - Variable: `%http_data`
     - Search: `"response":"(.*?)","source"`
     - Replace with: `$1`
     - Store result in: `%aria_response`
   - **Say** (action: Alert > Say)
     - Text: `%aria_response`
     - Engine: Default (or Google TTS)
     - Stream: Media

5. **Else If** `%http_code` equals `401`
   - **Say**: "Authentication failed. Check your ARIA token."

6. **Else** (network error or server error)
   - **Write File** (action: File > Write File)
     - File: `aria_queue.txt`
     - Text: `%TIMEMS|%voice_input`
     - Append: Yes
   - **Say**: "Your server is offline. I've saved your request and will process it when it's back."

7. **End If** (x2)

---

### Step 3: Create Task — "ARIA Health Check"

Pings the daemon periodically to track online status.

1. **HTTP Request**
   - Method: GET
   - URL: `%ARIA_HOST/health`
   - Timeout: 5 seconds
   - Store response code in: `%health_code`

2. **If** `%health_code` equals `200`
   - **Variable Set**: `%ARIA_ONLINE` to `1`
   - **If** file `aria_queue.txt` exists
     - **Perform Task**: "ARIA Drain Queue"
   - **End If**

3. **Else**
   - **Variable Set**: `%ARIA_ONLINE` to `0`

4. **End If**

---

### Step 4: Create Task — "ARIA Drain Queue"

Replays queued requests when the server comes back online.

1. **Read File**: `aria_queue.txt` into `%queue_data`

2. **If** `%queue_data` is set
   - **Variable Split**: `%queue_data` by newline
   - **For** each `%item` in `%queue_data()`
     - **Variable Split**: `%item` by `|`
     - **Variable Set**: `%queued_text` to `%item(2)`
     - **HTTP Request**
       - Method: POST
       - URL: `%ARIA_HOST/ask`
       - Headers: `Content-Type: application/json` and `Authorization: Bearer %ARIA_TOKEN`
       - Body: `{"text": "%queued_text"}`
       - Timeout: 180 seconds
       - Store response in: `%q_response`
       - Store response code in: `%q_code`
     - **If** `%q_code` equals `200`
       - **Variable Search Replace** (extract response as above)
       - **Say**: "From your queue: %q_aria_response"
     - **End If**
   - **End For**
   - **Delete File**: `aria_queue.txt`
   - **Say**: "All queued requests have been processed."

3. **End If**

---

### Step 5: Create Task — "ARIA Morning Brief"

Shortcut specifically for the morning briefing.

1. **HTTP Request**
   - Method: POST
   - URL: `%ARIA_HOST/ask`
   - Headers: `Content-Type: application/json` and `Authorization: Bearer %ARIA_TOKEN`
   - Body: `{"text": "good morning"}`
   - Timeout: 180 seconds
   - Store response in: `%http_data`
   - Store response code in: `%http_code`

2. **If** `%http_code` equals `200`
   - Extract and speak response (same as ARIA Ask step 4)

3. **Else**
   - **Say**: "I can't reach the server right now. Try again in a moment."

4. **End If**

---

### Step 6: Create Profiles (Triggers)

**Profile 1: ARIA Button**
- Trigger: Event > UI > Assistant (or a widget shortcut)
- Task: "ARIA Ask"

**Profile 2: Health Check (periodic)**
- Trigger: Time > Every 2 minutes
- Task: "ARIA Health Check"

**Profile 3: Morning Brief (optional auto-trigger)**
- Trigger: Time > 7:00 AM (or whenever you wake up)
- Condition: Day of week = Mon-Fri (optional)
- Task: "ARIA Morning Brief"

---

### Step 7: Create Home Screen Widget

1. Long press home screen > Widgets > Tasker > Task Shortcut
2. Select "ARIA Ask"
3. Name it "ARIA" with a microphone icon
4. Repeat for "ARIA Morning Brief" if desired

---

### Response Parsing Note

The JSON response from ARIA looks like:
```json
{"response": "The answer text here", "source": "claude"}
```

Tasker's HTTP Request stores the full JSON body. Use **Variable Search Replace** with regex to extract the response field, or use Tasker's **JSON Read** action if available in your version:
- JSON Read > Input: `%http_data` > Path: `.response` > Output: `%aria_response`

---

### Optional: Use Piper TTS (Server-Side Audio)

If you prefer higher-quality TTS from the server instead of Android's built-in TTS:

**Modify "ARIA Ask" task step 4:**

Replace the `Say` action with:

1. **HTTP Request**
   - Method: POST
   - URL: `%ARIA_HOST/ask/audio`
   - Headers: same as before (Content-Type + Authorization)
   - Body: `{"text": "%voice_input"}`
   - Timeout: 180 seconds
   - Output File: `/sdcard/aria_response.wav`

2. **Media > Play File**: `/sdcard/aria_response.wav`
   - Stream: Media

This sends the text to Claude, gets the response, and synthesizes it to WAV audio on the server using Piper TTS (lessac voice, 22kHz). The audio file is then played on the phone.

---

### Troubleshooting

- **"Connection refused"**: Check Tailscale is connected on both devices. Run `tailscale status` on slappy.
- **401 errors**: Token mismatch. Verify `%ARIA_TOKEN` matches `config.py`.
- **Timeouts**: Claude Code can take 5-15 seconds. The 180s timeout is generous — if it hits that, something is hung.
- **No speech output**: Check TTS engine in Android Settings > Accessibility > Text-to-speech. Google TTS recommended.
- **Queue not draining**: Ensure the health check profile is active and running every 2 minutes.
