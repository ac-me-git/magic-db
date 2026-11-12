// The only script magic-db writes itself. Everything works without it; it adds what
// HTML alone cannot do for the search suggestions and the Search button's menu:
// arrow keys, Escape, closing when you click somewhere else, and waiting for the
// program to come back after an update.

(function () {
  "use strict";

  var box = document.getElementById("q");
  var list = document.getElementById("suggestions");
  var menu = document.querySelector(".split-menu");

  function options() {
    return list ? Array.prototype.slice.call(list.querySelectorAll("a")) : [];
  }

  function clear() {
    if (list) list.innerHTML = "";
  }

  if (box && list) {
    box.addEventListener("keydown", function (event) {
      var links = options();
      if (event.key === "ArrowDown" && links.length) {
        event.preventDefault();
        links[0].focus();
      } else if (event.key === "Escape") {
        clear();
      }
    });

    list.addEventListener("keydown", function (event) {
      var links = options();
      var at = links.indexOf(document.activeElement);
      if (event.key === "ArrowDown") {
        event.preventDefault();
        (links[at + 1] || links[0]).focus();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        (at > 0 ? links[at - 1] : box).focus();
      } else if (event.key === "Escape") {
        clear();
        box.focus();
      }
    });

    // an emptied box has nothing to suggest (the request is not sent for no text)
    box.addEventListener("input", function () {
      if (box.value.trim().length < 2) clear();
    });
  }

  document.addEventListener("click", function (event) {
    if (list && !event.target.closest(".search-field")) clear();
    if (menu && menu.open && !event.target.closest(".split-menu")) menu.open = false;
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && menu && menu.open) menu.open = false;
  });

  // After an update the program restarts: wait until it has gone away and come back.
  var waiting = document.querySelector("[data-wait-restart]");
  if (waiting && window.fetch) {
    var wentAway = false;
    var started = Date.now();
    var poll = setInterval(function () {
      fetch("/healthz", { cache: "no-store" }).then(
        function (response) {
          if (response.ok && (wentAway || Date.now() - started > 15000)) {
            clearInterval(poll);
            window.location.href = waiting.getAttribute("data-wait-restart");
          }
        },
        function () {
          wentAway = true;
        }
      );
    }, 1000);
  }
})();
