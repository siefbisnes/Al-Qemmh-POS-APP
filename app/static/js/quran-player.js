(function () {
  "use strict";

  var audio = document.getElementById("quranAudioEl");
  if (!audio) return;

  var current = { reciterId: null, reciterName: null, filename: null, surahName: null };
  var playlist = [];
  var repeatMode = "off";
  var ui = null;
  var saveTimer = null;
  var initialized = false;
  var autoplayPending = false;
  var sessionFlag = "quranSessionStarted";
  var loginLaunchFlag = "quranLaunchAfterLogin";

  function byId(id) { return document.getElementById(id); }

  function formatTime(seconds) {
    if (!isFinite(seconds) || seconds < 0) seconds = 0;
    var minutes = Math.floor(seconds / 60);
    var secs = Math.floor(seconds % 60);
    var digits = "٠١٢٣٤٥٦٧٨٩";
    return String(minutes).replace(/[0-9]/g, function (d) { return digits[d]; }) + ":" +
      String(secs < 10 ? "0" + secs : secs).replace(/[0-9]/g, function (d) { return digits[d]; });
  }

  function getState() {
    return {
      reciterId: current.reciterId,
      reciterName: current.reciterName,
      filename: current.filename,
      surahName: current.surahName,
      playing: !audio.paused,
      position: audio.currentTime || 0,
      duration: audio.duration || 0,
      volume: audio.volume,
      repeatMode: repeatMode,
    };
  }

  function saveState(extra) {
    if (!current.filename) return;
    var payload = Object.assign({
      last_reciter: current.reciterId,
      last_surah: current.filename,
      last_position: audio.currentTime || 0,
      last_playing: !audio.paused,
    }, extra || {});
    fetch("/quran/api/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(function () {});
  }

  function scheduleSave() {
    if (saveTimer) return;
    saveTimer = setTimeout(function () { saveTimer = null; saveState(); }, 5000);
  }

  function broadcast() {
    var state = getState();
    window.dispatchEvent(new CustomEvent("quran:state", { detail: state }));
    window.dispatchEvent(new CustomEvent("quran:trackchange", { detail: state }));
  }

  function syncUI() {
    if (!ui) return;
    ui.surah.textContent = current.surahName || "—";
    ui.reciter.textContent = current.reciterName || "—";
    ui.currentTime.textContent = formatTime(audio.currentTime);
    ui.duration.textContent = formatTime(audio.duration);
    ui.seek.value = audio.duration ? Math.round(audio.currentTime / audio.duration * 1000) : 0;
    ui.volume.value = Math.round(audio.volume * 100);
    ui.toggle.querySelector(".icon-play").hidden = !audio.paused;
    ui.toggle.querySelector(".icon-pause").hidden = audio.paused;
    ui.repeat.classList.toggle("is-active", repeatMode !== "off");
    ui.repeatBadge.hidden = repeatMode !== "track";
  }

  function attachUI() {
    var bar = byId("quranPlayerBar");
    if (!bar) { ui = null; return; }
    ui = {
      bar: bar, surah: byId("quranPlayerSurah"), reciter: byId("quranPlayerReciter"),
      toggle: byId("quranPlayerToggle"), prev: byId("quranPlayerPrev"), next: byId("quranPlayerNext"),
      seek: byId("quranPlayerSeek"), currentTime: byId("quranPlayerCurrentTime"),
      duration: byId("quranPlayerDuration"), repeat: byId("quranPlayerRepeat"),
      repeatBadge: byId("quranPlayerRepeatBadge"), volume: byId("quranPlayerVolume"),
    };
    ui.toggle.addEventListener("click", function () {
      if (audio.paused) audio.play().catch(function () {}); else audio.pause();
    });
    ui.prev.addEventListener("click", playPrevious);
    ui.next.addEventListener("click", playNext);
    ui.repeat.addEventListener("click", function () {
      var modes = ["off", "track", "playlist"];
      repeatMode = modes[(modes.indexOf(repeatMode) + 1) % modes.length];
      fetch("/quran/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repeat_mode: repeatMode }) }).catch(function () {});
      syncUI(); broadcast();
    });
    ui.volume.addEventListener("input", function () { audio.volume = Number(ui.volume.value) / 100; syncUI(); });
    ui.volume.addEventListener("change", function () {
      fetch("/quran/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ volume: audio.volume }) }).catch(function () {});
    });
    ui.seek.addEventListener("input", function () {
      if (audio.duration) audio.currentTime = Number(ui.seek.value) / 1000 * audio.duration;
      syncUI();
    });
    syncUI();
    broadcast();
  }

  function loadPlaylist(reciterId) {
    return fetch("/quran/api/reciters/" + encodeURIComponent(reciterId))
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) { playlist = data && data.tracks || []; return playlist; })
      .catch(function () { playlist = []; return playlist; });
  }

  function requestPlayback() {
    audio.play().then(function () {
      autoplayPending = false;
    }).catch(function () {
      autoplayPending = true;
    });
  }

  function playTrack(reciterId, filename, reciterName, surahName, position, shouldPlay) {
    var track = playlist.filter(function (item) { return item.filename === filename; })[0];
    current = { reciterId: reciterId, reciterName: reciterName, filename: filename, surahName: track && track.surah_name || surahName };
    audio.src = "/quran/audio/" + encodeURIComponent(reciterId) + "/" + encodeURIComponent(filename);
    var onMetadata = function () {
      audio.removeEventListener("loadedmetadata", onMetadata);
      if (position) audio.currentTime = position;
      syncUI();
      if (shouldPlay) requestPlayback();
    };
    audio.addEventListener("loadedmetadata", onMetadata);
    syncUI();
    saveState({ last_position: position || 0 });
    broadcast();
  }

  function startTrack(reciterId, filename, reciterName, surahName, position, shouldPlay) {
    loadPlaylist(reciterId).then(function () { playTrack(reciterId, filename, reciterName, surahName, position, shouldPlay); });
  }

  function currentIndex() { return playlist.findIndex(function (item) { return item.filename === current.filename; }); }
  function playNext() {
    if (!playlist.length) return;
    var next = currentIndex() + 1;
    if (next >= playlist.length) { if (repeatMode !== "playlist") return; next = 0; }
    playTrack(current.reciterId, playlist[next].filename, current.reciterName, playlist[next].surah_name, 0, true);
  }
  function playPrevious() {
    if (!playlist.length) return;
    var previous = Math.max(0, currentIndex() - 1);
    playTrack(current.reciterId, playlist[previous].filename, current.reciterName, playlist[previous].surah_name, 0, true);
  }

  function initAudioEvents() {
    if (initialized) return;
    initialized = true;
    audio.addEventListener("play", function () { syncUI(); broadcast(); saveState(); });
    audio.addEventListener("pause", function () { syncUI(); broadcast(); saveState(); });
    audio.addEventListener("timeupdate", function () { syncUI(); scheduleSave(); });
    audio.addEventListener("loadedmetadata", function () { syncUI(); });
    audio.addEventListener("ended", function () {
      if (repeatMode === "track") { audio.currentTime = 0; requestPlayback(); } else playNext();
    });
    audio.addEventListener("error", function () { if (playlist.length > 1) playNext(); });
    window.addEventListener("pagehide", function () { saveState(); });
    window.addEventListener("app:content-replaced", attachUI);
    window.addEventListener("focus", function () { if (autoplayPending) requestPlayback(); });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && autoplayPending) requestPlayback();
    });
    document.addEventListener("pointerdown", function () {
      if (autoplayPending) requestPlayback();
    });
  }

  function initializeState() {
    var loginForm = document.querySelector("form.login-form");
    if (loginForm) {
      sessionStorage.removeItem(sessionFlag);
      loginForm.addEventListener("submit", function () {
        sessionStorage.setItem(loginLaunchFlag, "1");
      }, { once: true });
      return;
    }
    var launchedAfterLogin = sessionStorage.getItem(loginLaunchFlag) === "1";
    var freshLaunch = launchedAfterLogin || !sessionStorage.getItem(sessionFlag);
    fetch("/quran/api/state" + (freshLaunch ? "?autostart=1" : ""))
      .then(function (response) {
        if (!response.ok || response.redirected || response.url.indexOf("/login") !== -1) return null;
        return response.json();
      })
      .then(function (data) {
        if (!data) return;
        sessionStorage.setItem(sessionFlag, "1");
        sessionStorage.removeItem(loginLaunchFlag);
        if (!data.active) return;
        repeatMode = data.repeat_mode || "off";
        audio.volume = data.volume == null ? 0.2 : data.volume;
        startTrack(data.reciter.id, data.track.filename, data.reciter.name, data.track.surah_name, data.position || 0, !!data.autoplay);
      }).catch(function () {});
  }

  initAudioEvents();
  attachUI();
  initializeState();
  window.QuranPlayer = {
    playTrack: function (reciterId, filename, reciterName, surahName) { startTrack(reciterId, filename, reciterName, surahName, 0, true); },
    setVolume: function (value) { audio.volume = value; syncUI(); },
    setRepeatMode: function (mode) { repeatMode = mode; syncUI(); },
    getCurrent: getState,
    isReady: function () { return true; },
  };
})();
