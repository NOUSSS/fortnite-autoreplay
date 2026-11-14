// La fenêtre de l'ouverture se comporte comme le panneau du logiciel : Activer / Désactiver
// (ou F8), Pastille ou Panneau au repos, opacité au repos, réglages, Quitter, et un journal
// qui avance tout seul. Autour : le reflet des verres, la bascule 3D et le recul au défilement.
(function () {
  "use strict";

  function role(name, root) {
    return (root || document).querySelector('[data-role="' + name + '"]');
  }

  function roles(name) {
    return Array.prototype.slice.call(document.querySelectorAll('[data-role="' + name + '"]'));
  }

  var hero = role("hero");
  var stage = role("stage");
  var card = role("card");
  var pill = role("pill");
  var toggle = role("toggle");
  var settingsButton = role("settings");
  var reopen = role("reopen");
  var opacity = role("opacity");
  var opacityValue = role("opacity-value");
  var clicks = role("clicks");
  var games = role("games");
  var counting = role("counting");

  var reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
  var wide = matchMedia("(min-width: 961px)");

  // --- reflet : chaque verre s'éclaire du côté du pointeur --------------------------------
  var pointerX = innerWidth * 0.7;
  var pointerY = innerHeight * 0.4;
  var sheenFrame = 0;

  document.addEventListener("pointermove", function (event) {
    pointerX = event.clientX;
    pointerY = event.clientY;
    wake();
    if (sheenFrame) return;
    sheenFrame = requestAnimationFrame(function () {
      sheenFrame = 0;
      document.querySelectorAll(".glass").forEach(function (glass) {
        var box = glass.getBoundingClientRect();
        if (box.bottom < 0 || box.top > innerHeight) return;
        glass.style.setProperty("--mx", pointerX - box.left + "px");
        glass.style.setProperty("--my", pointerY - box.top + "px");
      });
    });
  }, { passive: true });

  if (!stage) return;

  // --- état de la fenêtre -----------------------------------------------------------------
  var state = {
    active: true,
    rest: stage.dataset.rest || "panel",
    hovered: false, // pointeur sur la fenêtre (ou doigt posé dessus, jusqu'au toucher suivant ailleurs)
    focused: false, // focus clavier dans la fenêtre
    settings: false,
    quit: false,
  };
  var leaveTimer = 0;

  function isOpen() {
    return !state.quit && (state.rest === "panel" || state.hovered || state.focused);
  }

  function render() {
    var open = isOpen();
    stage.toggleAttribute("data-open", open);
    if (!open) state.settings = false;
    stage.toggleAttribute("data-settings", state.settings);
    stage.toggleAttribute("data-quit", state.quit);
    settingsButton.setAttribute("aria-expanded", String(state.settings));
    reopen.hidden = !state.quit;
    var touched = state.hovered || state.focused || state.settings;
    stage.style.setProperty("--rest", touched ? "1" : String(opacity.value / 100));
    wake();
  }

  function hoverIn() {
    clearTimeout(leaveTimer);
    if (state.hovered) return;
    state.hovered = true;
    render();
  }

  // comme dans le logiciel : la carte attend un peu avant de se refermer
  function hoverOut() {
    clearTimeout(leaveTimer);
    leaveTimer = setTimeout(function () {
      state.hovered = false;
      render();
    }, 400);
  }

  // Une carte fermée laisse passer le pointeur : seule la pastille l'ouvre.
  stage.addEventListener("pointerover", function (event) {
    if (event.pointerType === "touch") return;
    if (isOpen() || pill.contains(event.target)) hoverIn();
  });

  stage.addEventListener("pointerleave", function (event) {
    if (event.pointerType === "touch") return;
    hoverOut();
  });

  // Au toucher, pas de survol : la fenêtre reste ouverte jusqu'à un toucher ailleurs.
  document.addEventListener("pointerdown", function (event) {
    if (event.pointerType !== "touch") return;
    var inside = stage.contains(event.target);
    if (inside && !isOpen() && !pill.contains(event.target)) return;
    if (inside === state.hovered && (inside || !state.settings)) return;
    clearTimeout(leaveTimer);
    state.hovered = inside;
    if (!inside) state.settings = false;
    render();
  });

  stage.addEventListener("focusin", function (event) {
    var keyboard = event.target.matches(":focus-visible");
    if (keyboard === state.focused) return;
    state.focused = keyboard;
    render();
  });

  stage.addEventListener("focusout", function (event) {
    if (event.relatedTarget && stage.contains(event.relatedTarget)) return;
    if (!state.focused) return;
    state.focused = false;
    render();
  });

  // la pastille au clavier : Entrée ouvre la carte et y porte le focus
  pill.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    state.focused = true;
    render();
    toggle.focus();
  });

  pill.addEventListener("click", function () {
    clearTimeout(leaveTimer);
    state.hovered = true;
    render();
  });

  // --- Activer / Désactiver ---------------------------------------------------------------
  function setActive(active) {
    state.active = active;
    roles("dot").forEach(function (dot) {
      dot.dataset.state = active ? "on" : "off";
    });
    roles("state").forEach(function (label) {
      label.textContent = active ? "Relance active" : "Relance désactivée";
    });
    toggle.textContent = active ? "Désactiver" : "Activer";
    toggle.classList.toggle("is-accent", !active);
  }

  toggle.addEventListener("click", function () {
    setActive(!state.active);
  });

  function typing(target) {
    return target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && state.settings) {
      state.settings = false;
      render();
      settingsButton.focus();
      return;
    }
    if (event.repeat || event.ctrlKey || event.altKey || event.metaKey || typing(event.target)) return;
    if (event.key === "F8" && !state.quit) {
      event.preventDefault();
      setActive(!state.active);
    } else if (event.key === "F9" && !state.quit) {
      event.preventDefault();
      quit();
    }
  });

  // --- réglages ---------------------------------------------------------------------------
  settingsButton.addEventListener("click", function () {
    state.settings = !state.settings;
    render();
  });

  // un clic ailleurs referme les réglages, comme un menu
  document.addEventListener("pointerdown", function (event) {
    if (!state.settings || stage.contains(event.target)) return;
    state.settings = false;
    render();
  });

  var segments = {
    rest: function (value) {
      state.rest = value;
      stage.dataset.rest = value;
      render();
    },
    afk: function (value) {
      roles("afk-dot").forEach(function (dot) {
        dot.dataset.state = value;
      });
    },
  };

  stage.querySelectorAll(".fp-seg").forEach(function (segment) {
    segment.addEventListener("click", function (event) {
      var choice = event.target.closest("button");
      if (!choice) return;
      segment.querySelectorAll("button").forEach(function (button) {
        button.setAttribute("aria-pressed", String(button === choice));
      });
      var apply = segments[segment.dataset.role];
      if (apply) apply(choice.dataset.value);
    });
  });

  function showOpacity() {
    var min = Number(opacity.min);
    var max = Number(opacity.max);
    opacity.style.setProperty("--fill", ((opacity.value - min) / (max - min)) * 100 + "%");
    opacityValue.textContent = opacity.value + " %";
  }

  opacity.addEventListener("input", function () {
    showOpacity();
    render();
  });

  // --- Quitter / Relancer -----------------------------------------------------------------
  function quit() {
    state.quit = true;
    state.settings = false;
    render();
    reopen.focus({ preventScroll: true });
  }

  role("quit").addEventListener("click", quit);

  reopen.addEventListener("click", function () {
    state.quit = false;
    render();
    if (reopen.matches(":focus-visible")) toggle.focus();
  });

  // --- journal : une partie qui finit, la suivante qui part --------------------------------
  // Chaque pas avance l'horloge du panneau : la partie dure une vingtaine de minutes,
  // la relance une minute. Les coffres comptés s'ajoutent au total et à « Dernières parties ».
  var clock = 21 * 60 + 42; // la dernière partie lancée à 21:42
  var gameStart = clock;
  var total = 1248;
  var step = 0;

  function hhmm(minutes) {
    var m = ((minutes % 1440) + 1440) % 1440;
    return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
  }

  function thousands(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  function prepend(list, time, text) {
    var item = document.createElement("li");
    var stamp = document.createElement("time");
    stamp.textContent = time;
    item.append(stamp, text);
    item.setAttribute("data-new", "");
    list.prepend(item);
    while (list.children.length > 3) list.lastElementChild.remove();
  }

  function countChests() {
    var found = 6 + Math.floor(Math.random() * 10);
    var started = gameStart;
    counting.hidden = false;
    setTimeout(function () {
      counting.hidden = true;
      total += found;
      roles("total").forEach(function (label) {
        label.textContent = thousands(total);
      });
      prepend(games, hhmm(started), found + " coffres");
    }, 1600);
  }

  function tick() {
    if (!state.active || state.quit || document.hidden) return;
    if (step === 0) {
      clock += 19 + Math.floor(Math.random() * 6);
      prepend(clicks, hhmm(clock), "Retourner au salon");
      countChests();
    } else if (step === 1) {
      prepend(clicks, hhmm(clock), "Continuer");
    } else {
      clock += 1;
      gameStart = clock;
      prepend(clicks, hhmm(clock), "Jouer");
    }
    step = (step + 1) % 3;
  }

  setInterval(tick, 4200);

  // --- bascule 3D : la fenêtre flotte, tournée vers le texte, et se redresse sous la main ---
  var tilt = { rx: 6, ry: -12, lift: 1 };
  var tiltFrame = 0;
  var lastTime = 0;
  var heroVisible = true;

  function tiltAllowed() {
    return wide.matches && !reduceMotion.matches && heroVisible && !document.hidden;
  }

  function measure() {
    stage.style.setProperty("--ox", card.offsetWidth / 2 + "px");
    stage.style.setProperty("--oy", card.offsetHeight / 2 + "px");
  }

  function frame(time) {
    tiltFrame = 0;
    if (!tiltAllowed()) {
      stage.style.setProperty("--tilt", "none");
      lastTime = 0;
      return;
    }
    var dt = lastTime ? Math.min(time - lastTime, 64) : 16;
    lastTime = time;
    var box = stage.getBoundingClientRect();
    var nx = Math.max(-1, Math.min(1, (pointerX - (box.left + box.width / 2)) / (innerWidth / 2)));
    var ny = Math.max(-1, Math.min(1, (pointerY - (box.top + box.height / 2)) / (innerHeight / 2)));
    var flat = state.hovered || state.focused || state.settings || state.quit;
    var ease = 1 - Math.exp(-dt / 140);
    tilt.rx += ((flat ? 0 : 6 - ny * 8) - tilt.rx) * ease;
    tilt.ry += ((flat ? 0 : -12 + nx * 10) - tilt.ry) * ease;
    tilt.lift += ((flat ? 0 : 1) - tilt.lift) * ease;
    var float = Math.sin(time / 1400) * 6 * tilt.lift;
    stage.style.setProperty(
      "--tilt",
      "perspective(1100px) translateY(" + float.toFixed(2) + "px) rotateX(" +
        tilt.rx.toFixed(2) + "deg) rotateY(" + tilt.ry.toFixed(2) + "deg)"
    );
    stage.style.setProperty("--streak", (60 - tilt.ry * 4).toFixed(1) + "%");
    tiltFrame = requestAnimationFrame(frame);
  }

  function wake() {
    if (!tiltFrame && tiltAllowed()) tiltFrame = requestAnimationFrame(frame);
  }

  // --- recul de l'ouverture au défilement ------------------------------------------------
  var scrollFrame = 0;

  function recede() {
    scrollFrame = 0;
    var progress = 0;
    if (wide.matches && !reduceMotion.matches) {
      progress = Math.max(0, Math.min(1, scrollY / (hero.offsetHeight * 0.85)));
    }
    hero.style.setProperty("--p", progress.toFixed(3));
  }

  addEventListener("scroll", function () {
    if (!scrollFrame) scrollFrame = requestAnimationFrame(recede);
  }, { passive: true });

  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      heroVisible = entries[entries.length - 1].isIntersecting;
      wake();
    }).observe(stage);
  }

  if ("ResizeObserver" in window) new ResizeObserver(measure).observe(card);

  [wide, reduceMotion].forEach(function (query) {
    query.addEventListener("change", function () {
      recede();
      wake();
    });
  });

  document.addEventListener("visibilitychange", wake);

  measure();
  showOpacity();
  render();
  recede();
})();
