import json
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import Request, urlopen

import obspython as obs

socket.setdefaulttimeout(10)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cookie": "SOCS=CAI; CONSENT=YES+; PREF=f6=40000&hl=zh-TW",
}

KICK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8",
    "Referer": "https://kick.com/",
    "Origin": "https://kick.com",
}

FONT_PRESETS = [
    ("Microsoft JhengHei + Segoe UI", "'Microsoft JhengHei', 'Segoe UI', sans-serif"),
    ("Noto Sans TC", "'Noto Sans TC', 'Microsoft JhengHei', sans-serif"),
    ("PingFang TC", "'PingFang TC', 'Microsoft JhengHei', sans-serif"),
    ("Arial", "Arial, 'Microsoft JhengHei', sans-serif"),
    ("Consolas", "Consolas, 'Microsoft JhengHei', monospace"),
    ("Impact", "Impact, 'Microsoft JhengHei', sans-serif"),
]

CONFIG_LOCK = threading.Lock()
RUNTIME_CONFIG = {
    "twitch_channel": "",
    "yt_channel": "",
    "kick_channel": "",
    "poll_seconds": 10,
    "show_debug": True,
    "font_family": "'Microsoft JhengHei', 'Segoe UI', sans-serif",
    "custom_font": "",
    "font_size": 24,
    "font_weight": 700,
}

SERVER_LOCK = threading.Lock()
HTTP_SERVER = None
SERVER_THREAD = None
SERVER_PORT = 8765


def _get_config_snapshot():
    with CONFIG_LOCK:
        return dict(RUNTIME_CONFIG)


def _update_config(**kwargs):
    with CONFIG_LOCK:
        RUNTIME_CONFIG.update(kwargs)


def _resolve_font_css(cfg: dict) -> str:
    selected = (cfg.get("font_family") or "").strip()
    custom = (cfg.get("custom_font") or "").strip()
    if selected == "__custom__":
        if custom:
            escaped = custom.replace("'", "\\'")
            return f"'{escaped}', 'Microsoft JhengHei', sans-serif"
        return "'Microsoft JhengHei', 'Segoe UI', sans-serif"
    return selected or "'Microsoft JhengHei', 'Segoe UI', sans-serif"


def _build_overlay_html(cfg: dict) -> str:
    tw_default = json.dumps(cfg["twitch_channel"])
    yt_default = json.dumps(cfg["yt_channel"])
    kick_default = json.dumps(cfg["kick_channel"])
    poll_ms = int(max(3, cfg["poll_seconds"])) * 1000
    show_debug = "true" if cfg["show_debug"] else "false"

    font_css = _resolve_font_css(cfg)
    font_size = int(max(12, min(120, cfg.get("font_size", 24))))
    font_weight = int(max(100, min(900, cfg.get("font_weight", 700))))
    gap_px = max(10, round(font_size * 0.75))
    item_gap_px = max(5, round(font_size * 0.33))
    icon_px = max(14, round(font_size * 1.0))
    divider_h_px = max(12, round(font_size * 0.83))

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OBS Twitch, YouTube & Kick Viewer Overlay</title>
    <style>
        body {{
            margin: 0;
            padding: 15px;
            background-color: transparent;
            font-family: {font_css};
            overflow: hidden;
            user-select: none;
        }}

        /* 無背景框，僅排列人數項目 */
        .overlay-container {{
            display: inline-flex;
            align-items: center;
            gap: {gap_px}px;
            background: transparent;
            border: none;
            box-shadow: none;
            font-family: {font_css};
            font-size: {font_size}px;
            font-weight: {font_weight};
            text-shadow: 0 2px 6px rgba(0, 0, 0, 0.85), 0 0 2px rgba(0, 0, 0, 0.9);
            transition: opacity 0.25s ease, transform 0.25s ease;
        }}

        .hidden {{
            opacity: 0;
            transform: translateY(-8px);
            pointer-events: none;
        }}

        .stat-item {{
            display: flex;
            align-items: center;
            gap: {item_gap_px}px;
        }}

        .divider {{
            width: 1px;
            height: {divider_h_px}px;
            background-color: rgba(255, 255, 255, 0.45);
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.8);
        }}

        .tabular-nums {{
            font-variant-numeric: tabular-nums;
            letter-spacing: 0.5px;
        }}

        .icon {{
            width: {icon_px}px;
            height: {icon_px}px;
            fill: currentColor;
            filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.85));
        }}

        /* 1. Twitch 人數（紫色 Logo + 紫色數字） */
        .twitch-color {{
            color: #bf94ff;
        }}

        /* 2. YouTube 人數（紅色圖示 + 紅色數字） */
        .youtube-color {{
            color: #ff4e45;
        }}

        /* 3. Kick 人數（Kick 綠圖示 + 綠色數字） */
        .kick-color {{
            color: #53fc18;
        }}

        .status-text {{
            font-size: 14px;
            font-weight: 500;
            color: #e0e0e0;
        }}
    </style>
</head>
<body>

    <div id="overlayBox" class="overlay-container hidden">
        <!-- 1. Twitch 觀看人數區塊 -->
        <div id="twitchSection" class="stat-item twitch-color" style="display: none;">
            <svg class="icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M11.571 4.714h1.715v5.143H11.57zm4.715 0H18v5.143h-1.714zM6 0L1.714 4.286v15.428h5.143V24l4.286-4.286h3.428L22.286 12V0zm14.571 11.143l-3.428 3.428h-3.429l-3 3v-3H6.857V1.714h13.714Z"/>
            </svg>
            <span id="twitchViewerText" class="tabular-nums">0</span>
        </div>

        <div id="dividerAfterTwitch" class="divider" style="display: none;"></div>

        <!-- 2. YouTube 觀看人數區塊 -->
        <div id="youtubeSection" class="stat-item youtube-color" style="display: none;">
            <svg class="icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
            </svg>
            <span id="youtubeViewerText" class="tabular-nums">0</span>
        </div>

        <div id="dividerAfterYoutube" class="divider" style="display: none;"></div>

        <!-- 3. Kick 觀看人數區塊 -->
        <div id="kickSection" class="stat-item kick-color" style="display: none;">
            <svg class="icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M3 2h6.5v6.2L14.8 2H22l-6.8 8.2L22.5 22h-7.4l-5.6-7.6V22H3V2z"/>
            </svg>
            <span id="kickViewerText" class="tabular-nums">0</span>
        </div>

        <span id="statusText" class="status-text" style="display: none;"></span>
    </div>

    <script>
        const urlParams = new URLSearchParams(window.location.search);
        const DEFAULT_TWITCH_CHANNEL = {tw_default};
        const DEFAULT_YT_CHANNEL_ID = {yt_default};
        const DEFAULT_KICK_CHANNEL = {kick_default};

        const twitchChannel = (urlParams.get('channel') || urlParams.get('id') || DEFAULT_TWITCH_CHANNEL).trim().toLowerCase();
        const ytChannelId = (urlParams.get('yt') || urlParams.get('yt_channel') || DEFAULT_YT_CHANNEL_ID).trim();
        const kickChannel = (urlParams.get('kick') || urlParams.get('kick_channel') || DEFAULT_KICK_CHANNEL).trim().toLowerCase();

        const SHOW_DEBUG_STATUS = {show_debug};
        const TWITCH_GQL_URL = "https://gql.twitch.tv/gql";
        const TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko";
        const NORMAL_POLL_MS = {poll_ms};
        const OFFLINE_POLL_MS = 3000;

        let twitchLive = false;
        let twitchViewers = 0;
        let twitchStatusMsg = "TW未設定";

        let youtubeLive = false;
        let youtubeViewers = 0;
        let youtubeStatusMsg = "YT未設定";

        let kickLive = false;
        let kickViewers = 0;
        let kickStatusMsg = "KICK未設定";

        let twitchTimer = null;
        let youtubeTimer = null;
        let kickTimer = null;

        let twitchBusy = false;
        let youtubeBusy = false;
        let kickBusy = false;

        const overlayBox = document.getElementById('overlayBox');
        const twitchSection = document.getElementById('twitchSection');
        const twitchViewerText = document.getElementById('twitchViewerText');
        const dividerAfterTwitch = document.getElementById('dividerAfterTwitch');
        const youtubeSection = document.getElementById('youtubeSection');
        const youtubeViewerText = document.getElementById('youtubeViewerText');
        const dividerAfterYoutube = document.getElementById('dividerAfterYoutube');
        const kickSection = document.getElementById('kickSection');
        const kickViewerText = document.getElementById('kickViewerText');
        const statusText = document.getElementById('statusText');

        function renderOverlay() {{
            const showTwitch = twitchLive && Boolean(twitchChannel) && twitchChannel !== "yourchannel";
            const showYoutube = youtubeLive && Boolean(ytChannelId);
            const showKick = kickLive && Boolean(kickChannel);

            twitchSection.style.display = showTwitch ? 'flex' : 'none';
            if (showTwitch) {{
                twitchViewerText.textContent = twitchViewers.toLocaleString();
            }}

            youtubeSection.style.display = showYoutube ? 'flex' : 'none';
            if (showYoutube) {{
                youtubeViewerText.textContent = youtubeViewers.toLocaleString();
            }}

            kickSection.style.display = showKick ? 'flex' : 'none';
            if (showKick) {{
                kickViewerText.textContent = kickViewers.toLocaleString();
            }}

            const hasAnyLiveData = showTwitch || showYoutube || showKick;

            if (SHOW_DEBUG_STATUS && !hasAnyLiveData) {{
                statusText.textContent = `${{twitchStatusMsg}} | ${{youtubeStatusMsg}} | ${{kickStatusMsg}}`;
                statusText.style.display = 'inline';
                dividerAfterTwitch.style.display = 'none';
                dividerAfterYoutube.style.display = 'none';
                overlayBox.classList.remove('hidden');
                return;
            }}

            statusText.style.display = 'none';
            dividerAfterTwitch.style.display = (showTwitch && (showYoutube || showKick)) ? 'block' : 'none';
            dividerAfterYoutube.style.display = (showYoutube && showKick) ? 'block' : 'none';

            if (hasAnyLiveData) {{
                overlayBox.classList.remove('hidden');
            }} else {{
                overlayBox.classList.add('hidden');
            }}
        }}

        async function pollTwitchOnce() {{
            if (twitchBusy) return;
            twitchBusy = true;

            try {{
                if (!twitchChannel || twitchChannel === "yourchannel") {{
                    twitchLive = false;
                    twitchViewers = 0;
                    twitchStatusMsg = "TW未設定";
                    renderOverlay();
                    return;
                }}

                const query = `query {{ user(login: "${{twitchChannel}}") {{ stream {{ viewersCount }} }} }}`;
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 5000);

                const response = await fetch(TWITCH_GQL_URL, {{
                    method: "POST",
                    headers: {{
                        "Client-ID": TWITCH_CLIENT_ID,
                        "Content-Type": "application/json"
                    }},
                    body: JSON.stringify({{ query }}),
                    signal: controller.signal
                }});

                clearTimeout(timeoutId);
                if (!response.ok) {{
                    twitchLive = false;
                    twitchStatusMsg = `TW HTTP ${{response.status}}`;
                    renderOverlay();
                    return;
                }}

                const json = await response.json();
                const stream = json?.data?.user?.stream;
                if (!stream) {{
                    twitchLive = false;
                    twitchViewers = 0;
                    twitchStatusMsg = "TW離線";
                    renderOverlay();
                    return;
                }}

                const count = Number(stream.viewersCount ?? 0);
                twitchLive = Number.isFinite(count) && count >= 0;
                twitchViewers = twitchLive ? count : 0;
                twitchStatusMsg = twitchLive ? "TW直播中" : "TW離線";
                renderOverlay();
            }} catch (error) {{
                twitchLive = false;
                twitchStatusMsg = "TW重試中";
                renderOverlay();
            }} finally {{
                twitchBusy = false;
                if (twitchTimer) clearTimeout(twitchTimer);
                const nextDelay = twitchLive ? NORMAL_POLL_MS : OFFLINE_POLL_MS;
                twitchTimer = setTimeout(pollTwitchOnce, nextDelay);
            }}
        }}

        async function pollYouTubeOnce() {{
            if (youtubeBusy) return;
            youtubeBusy = true;

            try {{
                if (!ytChannelId) {{
                    youtubeLive = false;
                    youtubeViewers = 0;
                    youtubeStatusMsg = "YT未設定";
                    renderOverlay();
                    return;
                }}

                const endpoint = `/api/yt?channel=${{encodeURIComponent(ytChannelId)}}&_t=${{Date.now()}}`;
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 12000);

                const response = await fetch(endpoint, {{
                    method: "GET",
                    cache: "no-store",
                    signal: controller.signal
                }});

                clearTimeout(timeoutId);
                if (!response.ok) {{
                    youtubeLive = false;
                    youtubeStatusMsg = `YT HTTP ${{response.status}}`;
                    renderOverlay();
                    return;
                }}

                const data = await response.json();
                const count = Number(data?.viewers);
                if (data?.viewers !== null && data?.viewers !== undefined && Number.isFinite(count) && count >= 0) {{
                    youtubeLive = true;
                    youtubeViewers = count;
                    youtubeStatusMsg = "YT直播中";
                }} else {{
                    youtubeLive = false;
                    youtubeViewers = 0;
                    youtubeStatusMsg = "YT偵測中";
                }}
                renderOverlay();
            }} catch (error) {{
                youtubeLive = false;
                youtubeStatusMsg = "YT重試中";
                renderOverlay();
            }} finally {{
                youtubeBusy = false;
                if (youtubeTimer) clearTimeout(youtubeTimer);
                const nextDelay = youtubeLive ? NORMAL_POLL_MS : OFFLINE_POLL_MS;
                youtubeTimer = setTimeout(pollYouTubeOnce, nextDelay);
            }}
        }}

        async function pollKickOnce() {{
            if (kickBusy) return;
            kickBusy = true;

            try {{
                if (!kickChannel) {{
                    kickLive = false;
                    kickViewers = 0;
                    kickStatusMsg = "KICK未設定";
                    renderOverlay();
                    return;
                }}

                const endpoint = `/api/kick?channel=${{encodeURIComponent(kickChannel)}}&_t=${{Date.now()}}`;
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 12000);

                const response = await fetch(endpoint, {{
                    method: "GET",
                    cache: "no-store",
                    signal: controller.signal
                }});

                clearTimeout(timeoutId);
                if (!response.ok) {{
                    kickLive = false;
                    kickStatusMsg = `KICK HTTP ${{response.status}}`;
                    renderOverlay();
                    return;
                }}

                const data = await response.json();
                const count = Number(data?.viewers);
                if (data?.isLive && data?.viewers !== null && data?.viewers !== undefined && Number.isFinite(count) && count >= 0) {{
                    kickLive = true;
                    kickViewers = count;
                    kickStatusMsg = "KICK直播中";
                }} else {{
                    kickLive = false;
                    kickViewers = 0;
                    kickStatusMsg = "KICK離線";
                }}
                renderOverlay();
            }} catch (error) {{
                kickLive = false;
                kickStatusMsg = "KICK重試中";
                renderOverlay();
            }} finally {{
                kickBusy = false;
                if (kickTimer) clearTimeout(kickTimer);
                const nextDelay = kickLive ? NORMAL_POLL_MS : OFFLINE_POLL_MS;
                kickTimer = setTimeout(pollKickOnce, nextDelay);
            }}
        }}

        document.addEventListener("visibilitychange", () => {{
            if (!document.hidden) {{
                pollTwitchOnce();
                pollYouTubeOnce();
                pollKickOnce();
            }}
        }});

        pollTwitchOnce();
        pollYouTubeOnce();
        pollKickOnce();
    </script>
</body>
</html>
"""


def _extract_json_object(text: str, marker: str):
    """從 HTML 內抽出 marker 後方的完整 JSON 物件（括號配對）。"""
    idx = text.find(marker)
    if idx == -1:
        return None
    start = text.find("{", idx + len(marker))
    if start == -1:
        return None

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except Exception:
                    return None
    return None


def _parse_int_str(raw):
    if raw is None:
        return None
    digits = re.sub(r"[^\d]", "", str(raw))
    if not digits:
        return None
    return int(digits)


def _extract_from_initial_data(initial_data: dict):
    """只從主播放器的 videoPrimaryInfoRenderer 抓『正在觀看 / watching』人數，避開側邊欄推薦影片。"""
    if not isinstance(initial_data, dict):
        return None

    try:
        contents = (
            initial_data.get("contents", {})
            .get("twoColumnWatchNextResults", {})
            .get("results", {})
            .get("results", {})
            .get("contents", [])
        )
    except Exception:
        contents = []

    for item in contents:
        primary = item.get("videoPrimaryInfoRenderer")
        if not primary:
            continue

        view_count_obj = primary.get("viewCount", {}).get("videoViewCountRenderer", {})
        is_live_flag = view_count_obj.get("isLive")
        if is_live_flag is False:
            return None

        orig = view_count_obj.get("originalViewCount")
        if orig is not None:
            val = _parse_int_str(orig)
            if val is not None:
                return val

        runs = view_count_obj.get("viewCount", {}).get("runs", [])
        if runs:
            full_text = "".join(r.get("text", "") for r in runs)
            if any(k in full_text.lower() for k in ("正在觀看", "watching")) and not any(
                k in full_text.lower() for k in ("等候", "waiting")
            ):
                val = _parse_int_str(runs[0].get("text"))
                if val is not None:
                    return val

        simple_text = view_count_obj.get("viewCount", {}).get("simpleText", "")
        if simple_text and any(k in simple_text.lower() for k in ("正在觀看", "watching")) and not any(
            k in simple_text.lower() for k in ("等候", "waiting")
        ):
            val = _parse_int_str(simple_text)
            if val is not None:
                return val

    return None


def _fetch_and_parse(url: str, expected_channel_id=None):
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=10) as resp:
        final_url = resp.geturl() or url
        html_text = resp.read().decode("utf-8", errors="ignore")

    m_v = re.search(r"[?&]v=([^&]+)", final_url)
    url_video_id = m_v.group(1) if m_v else None

    player_resp = _extract_json_object(html_text, "ytInitialPlayerResponse =")
    initial_data = _extract_json_object(html_text, "ytInitialData =")

    video_id = url_video_id
    is_live_now = False

    if isinstance(player_resp, dict):
        v_details = player_resp.get("videoDetails", {})
        microformat = player_resp.get("microformat", {}).get("playerMicroformatRenderer", {})
        live_details = microformat.get("liveBroadcastDetails", {})

        vid_in_player = v_details.get("videoId")
        if vid_in_player:
            video_id = vid_in_player

        owner_ch = v_details.get("channelId") or microformat.get("externalChannelId")
        if expected_channel_id and owner_ch and owner_ch != expected_channel_id:
            return None, None, False

        is_live_now = bool(live_details.get("isLiveNow")) or bool(v_details.get("isLive"))

        if is_live_now:
            viewers = _extract_from_initial_data(initial_data)
            if viewers is not None:
                return viewers, video_id, True

            m_cv = re.search(r'"concurrentViewers"\s*:\s*"?([0-9,]+)"?', json.dumps(player_resp))
            if m_cv:
                return int(m_cv.group(1).replace(",", "")), video_id, True

            vc = _parse_int_str(v_details.get("viewCount"))
            if vc is not None:
                return vc, video_id, True

    return None, video_id, is_live_now


def _fetch_yt_viewers(channel_or_video: str):
    target = channel_or_video.strip()
    if not target:
        return {"viewers": None, "videoId": None}

    expected_ch = target if target.startswith("UC") else None
    if expected_ch:
        primary_url = f"https://www.youtube.com/channel/{target}/live"
    else:
        primary_url = f"https://www.youtube.com/watch?v={target}"

    viewers, video_id, is_live_now = _fetch_and_parse(primary_url, expected_ch)
    if is_live_now and video_id and viewers is not None:
        return {"viewers": viewers, "videoId": video_id}

    if video_id and not primary_url.endswith(f"watch?v={video_id}"):
        backup_url = f"https://www.youtube.com/watch?v={video_id}"
        b_viewers, b_vid, b_live = _fetch_and_parse(backup_url, expected_ch)
        if b_live and b_vid and b_viewers is not None:
            return {"viewers": b_viewers, "videoId": b_vid}

    return {"viewers": None, "videoId": video_id}


def _fetch_kick_viewers(channel_slug: str):
    slug = channel_slug.strip().lower()
    if not slug:
        return {"isLive": False, "viewers": None}

    api_urls = [
        f"https://kick.com/api/v2/channels/{slug}",
        f"https://kick.com/api/v1/channels/{slug}",
    ]

    for api_url in api_urls:
        try:
            req = Request(api_url, headers=KICK_HEADERS)
            with urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
                data = json.loads(raw)

            livestream = data.get("livestream")
            if isinstance(livestream, dict):
                viewers = _parse_int_str(livestream.get("viewer_count"))
                if viewers is not None:
                    return {"isLive": True, "viewers": viewers}
            elif livestream is None and "livestream" in data:
                return {"isLive": False, "viewers": None}
        except Exception:
            continue

    try:
        page_url = f"https://kick.com/{slug}"
        req = Request(page_url, headers=HEADERS)
        with urlopen(req, timeout=10) as resp:
            html_text = resp.read().decode("utf-8", errors="ignore")

        m_live = re.search(r'"livestream"\s*:\s*\{.*?"viewer_count"\s*:\s*([0-9]+)', html_text, re.DOTALL)
        if m_live:
            return {"isLive": True, "viewers": int(m_live.group(1))}
    except Exception:
        pass

    return {"isLive": False, "viewers": None}


class _OverlayHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status_code: int, html: str):
        body = html.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path in ("/", "/overlay", "/overlay.html"):
            cfg = _get_config_snapshot()
            self._send_html(200, _build_overlay_html(cfg))
            return

        if parsed.path == "/api/kick":
            kick_id = (params.get("channel", [None])[0] or "").strip()
            if not kick_id:
                kick_id = _get_config_snapshot()["kick_channel"].strip()
            if not kick_id:
                self._send_json(400, {"error": "Missing ?channel= parameter", "isLive": False, "viewers": None})
                return
            try:
                result = _fetch_kick_viewers(kick_id)
                self._send_json(200, result)
            except Exception as e:
                self._send_json(502, {"error": str(e), "isLive": False, "viewers": None})
            return

        channel_id = (params.get("channel", [None])[0] or "").strip()
        raw_url = unquote(params.get("url", [None])[0] or "").strip()

        if not channel_id and raw_url:
            m_ch = re.search(r"/channel/(UC[^/?&]+)", raw_url)
            m_watch = re.search(r"[?&]v=([^&]+)", raw_url)
            if m_ch:
                channel_id = m_ch.group(1)
            elif m_watch:
                channel_id = m_watch.group(1)

        if not channel_id:
            channel_id = _get_config_snapshot()["yt_channel"].strip()

        if not channel_id:
            self._send_json(400, {"error": "Missing ?channel= parameter", "viewers": None})
            return

        try:
            result = _fetch_yt_viewers(channel_id)
            self._send_json(200, result)
        except Exception as e:
            self._send_json(502, {"error": str(e), "viewers": None})

    def log_message(self, format, *args):
        pass


def _start_background_server():
    global HTTP_SERVER, SERVER_THREAD
    with SERVER_LOCK:
        if HTTP_SERVER is not None:
            return
        try:
            server = HTTPServer(("127.0.0.1", SERVER_PORT), _OverlayHandler)
            server.timeout = 0.5
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            HTTP_SERVER = server
            SERVER_THREAD = thread
            obs.script_log(obs.LOG_INFO, f"[Overlay Script] Started at http://127.0.0.1:{SERVER_PORT}/overlay")
        except Exception as e:
            obs.script_log(obs.LOG_WARNING, f"[Overlay Script] Failed to start server: {e}")


def _stop_background_server():
    global HTTP_SERVER, SERVER_THREAD
    with SERVER_LOCK:
        if HTTP_SERVER is not None:
            try:
                HTTP_SERVER.shutdown()
                HTTP_SERVER.server_close()
            except Exception:
                pass
            HTTP_SERVER = None
            SERVER_THREAD = None
            obs.script_log(obs.LOG_INFO, "[Overlay Script] Stopped background server")


# ---------------- OBS 腳本生命週期與介面 ----------------

def script_description():
    return (
        "在背景自動啟動本機 Overlay 服務 (http://127.0.0.1:8765/overlay)，"
        "顯示 Twitch、YouTube 與 Kick 即時觀看人數，並支援字型與大小粗幼設定。"
    )


def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_text(props, "twitch_channel", "Twitch 頻道 ID", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(props, "yt_channel", "YouTube Channel ID (UC...)", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(props, "kick_channel", "Kick 頻道 ID", obs.OBS_TEXT_DEFAULT)

    font_list = obs.obs_properties_add_list(
        props, "font_family", "字型選擇", obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )
    for label, css_val in FONT_PRESETS:
        obs.obs_property_list_add_string(font_list, label, css_val)
    obs.obs_property_list_add_string(font_list, "自訂字型（下方輸入）", "__custom__")

    obs.obs_properties_add_text(props, "custom_font", "自訂字型名稱", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_int(props, "font_size", "字型大小 (px)", 12, 120, 1)

    weight_list = obs.obs_properties_add_list(
        props, "font_weight", "字型粗幼", obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_INT
    )
    for label, w_val in [
        ("400 - 正常 (Regular)", 400),
        ("500 - 中等 (Medium)", 500),
        ("600 - 半粗 (SemiBold)", 600),
        ("700 - 粗體 (Bold)", 700),
        ("800 - 特粗 (ExtraBold)", 800),
        ("900 - 黑體 (Black)", 900),
    ]:
        obs.obs_property_list_add_int(weight_list, label, w_val)

    obs.obs_properties_add_int(props, "poll_seconds", "上線後更新間隔（秒）", 3, 120, 1)
    obs.obs_properties_add_bool(props, "show_debug", "無直播時顯示狀態提示")
    return props


def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "twitch_channel", "")
    obs.obs_data_set_default_string(settings, "yt_channel", "")
    obs.obs_data_set_default_string(settings, "kick_channel", "")
    obs.obs_data_set_default_string(settings, "font_family", "'Microsoft JhengHei', 'Segoe UI', sans-serif")
    obs.obs_data_set_default_string(settings, "custom_font", "")
    obs.obs_data_set_default_int(settings, "font_size", 24)
    obs.obs_data_set_default_int(settings, "font_weight", 700)
    obs.obs_data_set_default_int(settings, "poll_seconds", 10)
    obs.obs_data_set_default_bool(settings, "show_debug", True)


def script_update(settings):
    _update_config(
        twitch_channel=obs.obs_data_get_string(settings, "twitch_channel").strip(),
        yt_channel=obs.obs_data_get_string(settings, "yt_channel").strip(),
        kick_channel=obs.obs_data_get_string(settings, "kick_channel").strip(),
        font_family=obs.obs_data_get_string(settings, "font_family").strip(),
        custom_font=obs.obs_data_get_string(settings, "custom_font").strip(),
        font_size=obs.obs_data_get_int(settings, "font_size"),
        font_weight=obs.obs_data_get_int(settings, "font_weight"),
        poll_seconds=obs.obs_data_get_int(settings, "poll_seconds"),
        show_debug=obs.obs_data_get_bool(settings, "show_debug"),
    )
    _start_background_server()


def script_load(settings):
    script_update(settings)


def script_unload():
    _stop_background_server()
