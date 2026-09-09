(function () {
  "use strict";

  // ---------------------------------------------------------------
  // Reciters grid page (templates/quran.html)
  // ---------------------------------------------------------------
  var grid = document.getElementById("quranReciterGrid");
  var searchInput = document.getElementById("quranSearchInput");
  var noResults = document.getElementById("quranNoResults");

  function filterReciters() {
    var q = searchInput.value.trim().toLowerCase();
    var visible = 0;
    grid.querySelectorAll(".quran-reciter-card").forEach(function (card) {
      var name = (card.dataset.reciterName || "").toLowerCase();
      var match = !q || name.indexOf(q) !== -1;
      card.style.display = match ? "" : "none";
      if (match) visible++;
    });
    if (noResults) noResults.hidden = visible !== 0;
  }

  if (grid && searchInput) {
    searchInput.addEventListener("input", filterReciters);
  }

  function wireRescanButton(btn) {
    if (!btn) return;
    var originalHTML = btn.innerHTML;
    btn.addEventListener("click", function () {
      btn.disabled = true;
      btn.style.opacity = ".6";
      fetch("/quran/api/rescan", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function () { window.location.reload(); })
        .catch(function () {
          btn.disabled = false;
          btn.style.opacity = "";
          btn.innerHTML = originalHTML;
        });
    });
  }
  wireRescanButton(document.getElementById("quranRescanBtn"));
  wireRescanButton(document.getElementById("quranRescanBtnEmpty"));

  // ---------------------------------------------------------------
  // Reciter playlist page (templates/quran_reciter.html)
  // ---------------------------------------------------------------
  var page = document.querySelector(".quran-page[data-reciter-id]");
  var trackList = document.getElementById("quranTrackList");
  var surahSearchInput = document.getElementById("quranSurahSearchInput");

  if (page && trackList) {
    var reciterId = page.dataset.reciterId;
    var reciterName = page.dataset.reciterName;

    function setRowPlayingState(row, playing) {
      var playIcon = row.querySelector(".icon-play");
      var pauseIcon = row.querySelector(".icon-pause");
      row.classList.toggle("is-playing", playing);
      if (playIcon) playIcon.hidden = playing;
      if (pauseIcon) pauseIcon.hidden = !playing;
    }

    function syncRows(state) {
      trackList.querySelectorAll(".quran-track-row").forEach(function (row) {
        var isThisTrack = state && state.reciterId === reciterId && state.filename === row.dataset.filename;
        setRowPlayingState(row, !!(isThisTrack && state.playing));
      });
    }

    trackList.addEventListener("click", function (e) {
      var row = e.target.closest(".quran-track-row");
      if (!row) return;

      // Clicking a row that's already playing toggles pause/resume
      // instead of restarting the track from zero.
      var current = window.QuranPlayer && window.QuranPlayer.getCurrent();
      if (current && current.reciterId === reciterId && current.filename === row.dataset.filename) {
        var toggleBtn = document.getElementById("quranPlayerToggle");
        if (toggleBtn) toggleBtn.click();
        return;
      }

      if (window.QuranPlayer) {
        window.QuranPlayer.playTrack(reciterId, row.dataset.filename, reciterName, row.dataset.surahName);
      }
    });

    window.addEventListener("quran:trackchange", function (e) {
      syncRows(e.detail);
    });

    // In case playback was already underway (started from another
    // page) before this page's listener attached.
    if (window.QuranPlayer) {
      syncRows(window.QuranPlayer.getCurrent());
    }

    if (surahSearchInput) {
      var noResultsEl = document.getElementById("quranNoResults");
      surahSearchInput.addEventListener("input", function () {
        var q = surahSearchInput.value.trim().toLowerCase();
        var visible = 0;
        trackList.querySelectorAll(".quran-track-row").forEach(function (row) {
          var name = (row.dataset.surahName || "").toLowerCase();
          var match = !q || name.indexOf(q) !== -1;
          row.style.display = match ? "" : "none";
          if (match) visible++;
        });
        if (noResultsEl) noResultsEl.hidden = visible !== 0;
      });
    }
  }
})();
