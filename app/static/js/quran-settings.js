(function () {
  "use strict";

  var section = document.getElementById("quran-settings");
  if (!section) return; // snippet not present on this page

  var autoplayToggle = document.getElementById("quranAutoplayToggle");
  var reciterSelect = document.getElementById("quranDefaultReciterSelect");
  var volumeSlider = document.getElementById("quranVolumeSlider");
  var repeatSelect = document.getElementById("quranRepeatSelect");
  var folderInput = document.getElementById("quranFolderInput");
  var folderSaveBtn = document.getElementById("quranFolderSaveBtn");
  var folderStatus = document.getElementById("quranFolderStatus");

  function saveSettings(patch) {
    return fetch("/quran/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
  }

  function loadAll() {
    Promise.all([
      fetch("/quran/api/library").then(function (r) { return r.json(); }),
      fetch("/quran/api/settings").then(function (r) { return r.json(); }),
    ]).then(function (results) {
      var library = results[0];
      var settings = results[1];

      reciterSelect.innerHTML = "";
      library.reciters.forEach(function (r) {
        var opt = document.createElement("option");
        opt.value = r.id;
        opt.textContent = r.name;
        reciterSelect.appendChild(opt);
      });
      if (settings.default_reciter) reciterSelect.value = settings.default_reciter;

      autoplayToggle.checked = !!settings.autoplay;
      volumeSlider.value = Math.round((settings.volume || 0) * 100);
      repeatSelect.value = settings.repeat_mode || "off";
      folderInput.value = settings.quran_dir || "";
    }).catch(function () {});
  }

  autoplayToggle.addEventListener("change", function () {
    saveSettings({ autoplay: autoplayToggle.checked });
  });
  reciterSelect.addEventListener("change", function () {
    saveSettings({ default_reciter: reciterSelect.value });
  });
  volumeSlider.addEventListener("input", function () {
    if (window.QuranPlayer) window.QuranPlayer.setVolume(Number(volumeSlider.value) / 100);
  });
  volumeSlider.addEventListener("change", function () {
    saveSettings({ volume: Number(volumeSlider.value) / 100 });
  });
  repeatSelect.addEventListener("change", function () {
    if (window.QuranPlayer) window.QuranPlayer.setRepeatMode(repeatSelect.value);
    saveSettings({ repeat_mode: repeatSelect.value });
  });
  folderSaveBtn.addEventListener("click", function () {
    folderStatus.textContent = "جارٍ الحفظ...";
    fetch("/quran/api/folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: folderInput.value.trim() }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          folderStatus.textContent = data.error;
        } else {
          folderStatus.textContent = "تم الحفظ - تم العثور على " + data.reciter_count + " شيخ.";
        }
      })
      .catch(function () { folderStatus.textContent = "تعذر الحفظ."; });
  });

  loadAll();
})();
