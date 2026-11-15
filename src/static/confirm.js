/* StudyMore: asks "are you sure?" before leaving a group.
   Works on every page (group page, My Groups, dashboard) because it watches all form submits. */
(function () {
    function ask(opts) {
        return new Promise(function (resolve) {
            if (!document.createElement("dialog").showModal) {
                resolve(window.confirm(opts.body));
                return;
            }
            var d = document.createElement("dialog");
            d.className = "sm-confirm";
            d.setAttribute("aria-label", opts.title);
            d.innerHTML =
                "<h2></h2><p></p>" +
                '<div class="sm-confirm-actions">' +
                '<button type="button" class="secondary" data-r="0">Stay</button>' +
                '<button type="button" class="primary" data-r="1"></button>' +
                "</div>";
            d.querySelector("h2").textContent = opts.title;
            d.querySelector("p").textContent = opts.body;
            d.querySelector('[data-r="1"]').textContent = opts.confirm;
            d.addEventListener("click", function (e) {
                var r = e.target.getAttribute && e.target.getAttribute("data-r");
                if (r !== null) { d.close(r); }
            });
            d.addEventListener("close", function () {
                var ok = d.returnValue === "1";
                d.remove();
                resolve(ok);
            });
            document.body.appendChild(d);
            d.showModal();
        });
    }

    document.addEventListener("submit", function (e) {
        var form = e.target;
        if (form.dataset.confirmed) { delete form.dataset.confirmed; return; }

        var action = form.getAttribute("action") || "";
        var btn = e.submitter;
        var label = btn ? (btn.textContent || btn.value || "") : "";
        var isLeave = /\/leave\/?$/.test(action) || /^\s*leave\b/i.test(label);
        if (!isLeave) { return; }

        e.preventDefault();
        ask({
            title: "Leave this group?",
            body: "You will be removed from the group. If it is private, you will need a new invitation to come back.",
            confirm: "Leave group"
        }).then(function (ok) {
            if (!ok) { return; }
            form.dataset.confirmed = "1";
            if (form.requestSubmit) { form.requestSubmit(btn || undefined); } else { form.submit(); }
        });
    }, true);
})();