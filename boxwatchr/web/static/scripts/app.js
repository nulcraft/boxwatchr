(function () {
    "use strict";

    function getCookie(name) {
        var match = document.cookie.match(new RegExp("(?:^|; )" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "=([^;]*)"));
        return match ? decodeURIComponent(match[1]) : null;
    }

    function setCookie(name, value, days) {
        var expires = new Date(Date.now() + days * 864e5).toUTCString();
        document.cookie = name + "=" + encodeURIComponent(value) + "; expires=" + expires + "; path=/; SameSite=Strict";
    }

    fetch("/api/version/check")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
            if (!data || !data.update_available) return;

            var latest = data.latest;
            if (getCookie("bw_skip_version") === latest) return;

            var title = document.getElementById("update-toast-title");
            var body = document.getElementById("update-toast-body");
            if (!title || !body) return;

            title.textContent = "Update " + latest + " Available";

            var notesHtml = "";
            if (data.release_notes && typeof marked !== "undefined") {
                notesHtml = "<div class=\"update-toast-notes\">" + marked.parse(data.release_notes) + "</div>";
            }

            body.innerHTML = notesHtml;

            var el = document.getElementById("update-toast");
            if (!el) return;

            var closeBtn = el.querySelector("[data-bs-dismiss=\"toast\"]");
            if (closeBtn) {
                closeBtn.addEventListener("click", function () {
                    setCookie("bw_skip_version", latest, 30);
                });
            }

            new bootstrap.Toast(el, { autohide: false }).show();
        })
        .catch(function () {});
}());
