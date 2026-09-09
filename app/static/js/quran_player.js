(function () {
  "use strict";

  var token = new URLSearchParams(window.location.search).get("desktop_token") || "";
  var audio = document.getElementById("audio");
  var state = { reciters: [], selected: null, playlist: [], current: null, repeat: "off" };
  var els = {};
  var autoplayPending = false;

  function api(path, options) {
    var join = path.indexOf("?") === -1 ? "?" : "&";
    return fetch(path + join + "desktop_token=" + encodeURIComponent(token), options);
  }
  function id(name) { return document.getElementById(name); }
  function time(value) {
    value = isFinite(value) && value > 0 ? value : 0;
    var mins = Math.floor(value / 60), secs = Math.floor(value % 60);
    return String(mins).replace(/[0-9]/g, function (d) { return "٠١٢٣٤٥٦٧٨٩"[d]; }) + ":" + String(secs < 10 ? "0" + secs : secs).replace(/[0-9]/g, function (d) { return "٠١٢٣٤٥٦٧٨٩"[d]; });
  }
  function audioUrl(reciter, filename) {
    return "/quran/audio/" + encodeURIComponent(reciter) + "/" + encodeURIComponent(filename) + "?desktop_token=" + encodeURIComponent(token);
  }
  function currentTrack() { return state.current; }

  function renderReciters() {
    var query = els.librarySearch.value.trim().toLowerCase();
    els.reciterGrid.replaceChildren();
    state.reciters.filter(function (r) { return !query || r.name.toLowerCase().indexOf(query) !== -1; }).forEach(function (reciter) {
      var card = document.createElement("button"); card.className = "reciter-card"; card.type = "button";
      card.innerHTML = '<span class="reciter-card-icon">♫</span><span class="reciter-card-bottom"><span><strong></strong><small></small></span><span class="card-play"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"></path></svg></span></span>';
      card.querySelector("strong").textContent = reciter.name;
      card.querySelector("small").textContent = reciter.track_count + " سورة";
      card.addEventListener("click", function () { openPlaylist(reciter.id); });
      els.reciterGrid.appendChild(card);
    });
    els.libraryEmpty.hidden = state.reciters.length > 0;
  }

  function renderPlaylist() {
    var query = els.playlistSearch.value.trim().toLowerCase();
    els.trackList.replaceChildren();
    state.playlist.filter(function (track) { return !query || (track.surah_name || "").toLowerCase().indexOf(query) !== -1; }).forEach(function (track, index) {
      var row = document.createElement("div"); row.className = "track-row" + (state.current && state.current.filename === track.filename && state.current.reciterId === state.selected.id ? " active" : "");
      row.innerHTML = '<span class="track-number"></span><span class="track-name"></span><button class="track-play" type="button" aria-label="تشغيل"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"></path></svg></button>';
      row.querySelector(".track-number").textContent = String(track.surah_number || index + 1).padStart(2, "0");
      row.querySelector(".track-name").textContent = track.surah_name;
      row.addEventListener("click", function () { play(track); });
      els.trackList.appendChild(row);
    });
  }

  function openPlaylist(reciterId) {
    api("/quran/api/reciters/" + encodeURIComponent(reciterId)).then(function (response) { return response.json(); }).then(function (reciter) {
      state.selected = reciter; state.playlist = reciter.tracks || [];
      els.playlistTitle.textContent = reciter.name; els.playlistCount.textContent = reciter.track_count + " سورة";
      els.libraryView.hidden = true; els.playlistView.hidden = false; els.settingsPanel.hidden = true;
      renderPlaylist();
    });
  }

  function updateNowPlaying() {
    var active = !!state.current;
    els.idleInfo.hidden = active; els.activeInfo.hidden = !active;
    if (active) { els.currentSurah.textContent = state.current.surahName; els.currentReciter.textContent = state.current.reciterName; }
    els.playIcon.hidden = !audio.paused; els.pauseIcon.hidden = audio.paused;
    els.currentTime.textContent = time(audio.currentTime); els.duration.textContent = time(audio.duration);
    els.progress.value = audio.duration ? Math.round(audio.currentTime / audio.duration * 1000) : 0;
    els.volume.value = Math.round(audio.volume * 100);
    if (state.selected && !els.playlistView.hidden) renderPlaylist();
  }

  function play(track, autoplay) {
    var reciter = state.selected || state.reciters.filter(function (r) { return r.id === track.reciterId; })[0];
    if (!reciter) return;
    state.selected = reciter; state.playlist = reciter.tracks || state.playlist;
    state.current = { reciterId: reciter.id, reciterName: reciter.name, filename: track.filename, surahName: track.surah_name };
    audio.src = audioUrl(reciter.id, track.filename); audio.load(); updateNowPlaying();
    audio.addEventListener("loadedmetadata", function once() { audio.removeEventListener("loadedmetadata", once); if (autoplay !== false) audio.play().catch(function () {}); }, { once: true });
    saveState();
  }

  function selectTrackFromData(data, autoplay) {
    if (!data || !data.active || !data.track) return;
    api("/quran/api/reciters/" + encodeURIComponent(data.reciter.id)).then(function (response) { return response.json(); }).then(function (reciter) {
      state.selected = reciter; state.playlist = reciter.tracks || [];
      var track = state.playlist.filter(function (item) { return item.filename === data.track.filename; })[0] || data.track;
      state.current = { reciterId: reciter.id, reciterName: reciter.name, filename: track.filename, surahName: track.surah_name };
      audio.src = audioUrl(reciter.id, track.filename); audio.volume = data.volume == null ? 0.2 : data.volume; audio.autoplay = !!autoplay; audio.load();
      audio.addEventListener("loadedmetadata", function once() { audio.removeEventListener("loadedmetadata", once); if (data.position) audio.currentTime = data.position; if (autoplay) requestAutoplay(); updateNowPlaying(); }, { once: true });
      updateNowPlaying();
    });
  }

  function requestAutoplay() {
    autoplayPending = true;
    audio.play().then(function () { autoplayPending = false; }).catch(function () {});
  }

  function retryAutoplay() {
    if (autoplayPending && audio.paused && state.current) requestAutoplay();
  }

  function saveState() {
    if (!state.current) return;
    api("/quran/api/state", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ last_reciter: state.current.reciterId, last_surah: state.current.filename, last_position: audio.currentTime || 0, last_playing: !audio.paused }) });
  }
  function stopPlayback() {
    audio.pause();
    audio.currentTime = 0;
    api("/quran/api/state", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ last_reciter: state.current && state.current.reciterId || "", last_surah: state.current && state.current.filename || "", last_position: 0, last_playing: false }) });
    state.current = null;
    updateNowPlaying();
  }
  function next() {
    if (!state.playlist.length || !state.current) return;
    var index = state.playlist.findIndex(function (t) { return t.filename === state.current.filename; });
    if (index + 1 >= state.playlist.length) { if (state.repeat !== "playlist") return; index = -1; }
    play(state.playlist[index + 1]);
  }
  function previous() { if (!state.playlist.length || !state.current) return; var index = Math.max(0, state.playlist.findIndex(function (t) { return t.filename === state.current.filename; }) - 1); play(state.playlist[index]); }

  function loadSettings() {
    return Promise.all([api("/quran/api/library").then(function (r) { return r.json(); }), api("/quran/api/settings").then(function (r) { return r.json(); })]).then(function (values) {
      state.reciters = values[0].reciters || []; var settings = values[1]; state.repeat = settings.repeat_mode || "off";
      els.autoplaySetting.checked = !!settings.autoplay; els.volumeSetting.value = Math.round((settings.volume || .2) * 100); els.volume.value = els.volumeSetting.value; els.repeatSetting.value = state.repeat; els.folderSetting.value = settings.quran_dir || "";
      els.reciterSetting.replaceChildren(); state.reciters.forEach(function (reciter) { var option = document.createElement("option"); option.value = reciter.id; option.textContent = reciter.name; els.reciterSetting.appendChild(option); }); els.reciterSetting.value = settings.default_reciter || (state.reciters[0] && state.reciters[0].id) || "";
      renderReciters();
    });
  }

  function wire() {
    els.libraryView = id("libraryView"); els.playlistView = id("playlistView"); els.reciterGrid = id("reciterGrid"); els.libraryEmpty = id("libraryEmpty"); els.librarySearch = id("librarySearch"); els.playlistSearch = id("playlistSearch"); els.trackList = id("trackList"); els.playlistTitle = id("playlistTitle"); els.playlistCount = id("playlistCount"); els.settingsPanel = id("settingsPanel"); els.idleInfo = id("idleInfo"); els.activeInfo = id("activeInfo"); els.currentSurah = id("currentSurah"); els.currentReciter = id("currentReciter"); els.playIcon = id("playIcon"); els.pauseIcon = id("pauseIcon"); els.currentTime = id("currentTime"); els.duration = id("duration"); els.progress = id("progress"); els.volume = id("volume"); els.autoplaySetting = id("autoplaySetting"); els.reciterSetting = id("reciterSetting"); els.volumeSetting = id("volumeSetting"); els.repeatSetting = id("repeatSetting"); els.folderSetting = id("folderSetting"); els.settingStatus = id("settingStatus");
    id("refreshLibrary").onclick = function () { loadSettings(); }; id("emptyRefresh").onclick = function () { loadSettings(); }; id("settingsToggle").onclick = function () { els.settingsPanel.hidden = false; }; id("settingsClose").onclick = function () { els.settingsPanel.hidden = true; }; id("backToLibrary").onclick = function () { els.playlistView.hidden = true; els.libraryView.hidden = false; renderReciters(); };
    els.librarySearch.oninput = renderReciters; els.playlistSearch.oninput = renderPlaylist; id("togglePlayback").onclick = function () { if (!state.current) return; if (audio.paused) audio.play().catch(function () {}); else audio.pause(); }; id("stopPlayback").onclick = stopPlayback; id("previousTrack").onclick = previous; id("nextTrack").onclick = next;
    els.progress.oninput = function () { if (audio.duration) audio.currentTime = Number(els.progress.value) / 1000 * audio.duration; updateNowPlaying(); }; els.volume.oninput = function () { audio.volume = Number(els.volume.value) / 100; els.volumeSetting.value = els.volume.value; }; els.volume.onchange = function () { api("/quran/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ volume: audio.volume }) }); };
    els.autoplaySetting.onchange = function () { api("/quran/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ autoplay: els.autoplaySetting.checked }) }); }; els.reciterSetting.onchange = function () { api("/quran/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ default_reciter: els.reciterSetting.value }) }); }; els.volumeSetting.oninput = function () { els.volume.value = els.volumeSetting.value; audio.volume = Number(els.volume.value) / 100; }; els.repeatSetting.onchange = function () { state.repeat = els.repeatSetting.value; api("/quran/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repeat_mode: state.repeat }) }); }; id("saveFolder").onclick = function () { api("/quran/api/folder", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: els.folderSetting.value.trim() }) }).then(function (r) { return r.json(); }).then(function (data) { els.settingStatus.textContent = data.error || "تم حفظ المجلد وتحديث المكتبة."; loadSettings(); }); };
    audio.onplay = function () { autoplayPending = false; updateNowPlaying(); }; audio.onpause = function () { updateNowPlaying(); saveState(); }; audio.ontimeupdate = updateNowPlaying; audio.onloadedmetadata = updateNowPlaying; audio.oncanplay = function () { retryAutoplay(); }; audio.onended = function () { if (state.repeat === "track") { audio.currentTime = 0; requestAutoplay(); } else next(); }; window.addEventListener("focus", retryAutoplay); document.addEventListener("visibilitychange", function () { if (!document.hidden) retryAutoplay(); }); window.onbeforeunload = function () { stopPlayback(); if (window.pywebview && window.pywebview.api && window.pywebview.api.stop_quran_playback) window.pywebview.api.stop_quran_playback(); };
  }

  wire(); loadSettings().then(function () { return api("/quran/api/state?autostart=1").then(function (r) { return r.json(); }); }).then(function (autostart) { selectTrackFromData(autostart, !!autostart.autoplay); }).catch(function () {});
})();
