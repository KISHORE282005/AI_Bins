/* Material Bin Recommendation System - client logic.
 *
 * Holds the analysis payload in memory and renders it: summary metrics, the
 * bin rule master, a searchable / sortable / paginated results table, the
 * validation log and the logic guide carried through from the workbook.
 *
 * Two payload shapes are handled, chosen by `mode` in the response:
 *
 *   "input"   an Input sheet matched against the bin rule master; the table
 *             shows demand volume, demand weight and the two utilisations
 *   "master"  Part Requirements matched against the workbook's own bin list
 *
 * No recommendation logic lives here. Every bin, status, utilisation figure
 * and reason string is produced by the Python rule engine and only rendered.
 */

(function () {
  "use strict";

  var state = {
    mode: "master",
    rows: [],
    bins: [],
    rules: [],
    issues: [],
    guide: [],
    summary: null,
    download: null,
    filtered: [],
    page: 1,
    pageSize: 25,
    sortKey: null,
    sortDir: 1,
    rulesHidden: false
  };

  var el = function (id) { return document.getElementById(id); };

  // ---------------------------------------------------------- formatting

  /* Thousands grouping (en-US) is used deliberately rather than the browser
     locale, so the table matches the figures quoted in the reason text and in
     the exported workbook. */
  function num(value, decimals) {
    if (value === null || value === undefined || value === "") return "—";
    var n = Number(value);
    if (!isFinite(n)) return "—";
    return n.toLocaleString("en-US", {
      minimumFractionDigits: decimals || 0,
      maximumFractionDigits: decimals || 0
    });
  }

  function compactVolume(value) {
    if (value === null || value === undefined) return "—";
    var n = Number(value);
    if (!isFinite(n)) return "—";
    if (n >= 1e9) return (n / 1e9).toFixed(2) + " m³";        // 1e9 mm3 = 1 m3
    if (n >= 1e3) return (n / 1e3).toFixed(1) + " cm³";       // 1e3 mm3 = 1 cm3
    return n.toFixed(0) + " mm³";
  }

  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function dims(a, b, c) {
    if (a === null || b === null || c === null ||
        a === undefined || b === undefined || c === undefined) return "—";
    return num(a) + " × " + num(b) + " × " + num(c);
  }

  /* Utilisation bar. Takes a percentage (0-100), not a fraction. */
  function utilBar(pct) {
    if (pct === null || pct === undefined) return "—";
    var cls = pct >= 95 ? "is-full" : (pct >= 80 ? "is-high" : "");
    var width = Math.max(0, Math.min(100, pct));
    return '<div class="util">' +
      '<span class="util-bar"><span class="util-fill ' + cls + '" style="width:' + width.toFixed(1) + '%"></span></span>' +
      '<span class="util-text">' + pct.toFixed(1) + "%</span></div>";
  }

  function badge(kind, text) {
    return '<span class="badge badge-' + kind + '">' + esc(text) + "</span>";
  }

  // ------------------------------------------------------------- columns

  /* One descriptor per mode: the table header, how each cell is drawn, which
     fields the search box looks at and which statuses the filter offers. */
  var VIEWS = {
    master: {
      searchPlaceholder: "Search part number, description or bin",
      searchFields: ["part_number", "description", "bin_suggestion"],
      statuses: [
        ["all", "All statuses"],
        ["ASSIGNED", "Assigned only"],
        ["UNASSIGNED", "No suitable bin"],
        ["ERROR", "Data errors"]
      ],
      rowClass: function (r) {
        return r.status === "UNASSIGNED" ? "row-unassigned"
             : (r.status === "ERROR" ? "row-error" : "");
      },
      columns: [
        { label: "Part Number", sort: "part_number", cls: "mono strong",
          cell: function (r) { return esc(r.part_number); } },
        { label: "Description", sort: "description",
          cell: function (r) { return esc(r.description); } },
        { label: "L × B × W (mm)", cls: "num mono",
          cell: function (r) { return dims(r.length, r.breadth, r.width); } },
        { label: "Total Volume (mm³)", sort: "unit_volume", cls: "num mono",
          cell: function (r) { return num(r.unit_volume); } },
        { label: "ROP", sort: "rop", cls: "num mono strong",
          cell: function (r) { return num(r.rop); } },
        { label: "Required Volume (mm³)", sort: "required_volume", cls: "num mono",
          cell: function (r) { return num(r.required_volume); } },
        { label: "Wt/Unit (kg)", sort: "weight_per_unit", cls: "num mono",
          cell: function (r) { return num(r.weight_per_unit, 2); } },
        { label: "Required Weight (kg)", sort: "required_weight", cls: "num mono",
          cell: function (r) { return num(r.required_weight, 2); } },
        { label: "Bin Suggestion", sort: "bin_suggestion",
          cell: function (r) {
            if (r.status === "ERROR") return badge("warn", "Data Error");
            if (r.status !== "ASSIGNED") return badge("bad", "No Suitable Bin");
            return badge("ok", r.bin_suggestion) +
              (r.orientation_used ? '<span class="tag-rotated">rotated</span>' : "") +
              (r.location ? '<div class="muted cell-note">' + esc(r.location) + "</div>" : "");
          } },
        { label: "Utilisation", cls: "num",
          cell: function (r) {
            return utilBar(r.volume_utilisation === null || r.volume_utilisation === undefined
              ? null : r.volume_utilisation * 100);
          } },
        { label: "Suggestion Reason", cls: "reason-cell", headCls: "reason-col",
          title: function (r) { return r.reason; },
          cell: function (r) { return '<div class="clamp">' + esc(r.reason) + "</div>"; } }
      ]
    },

    input: {
      searchPlaceholder: "Search SAP No, DWG No, description, recommended bin or status",
      searchFields: ["sap_no", "dwg_no", "description", "recommended_bin", "status"],
      statuses: [
        ["all", "All statuses"],
        ["Matched", "Matched only"],
        ["No Suitable Bin", "No suitable bin"],
        ["Invalid Data", "Invalid data"]
      ],
      rowClass: function (r) {
        return r.status === "No Suitable Bin" ? "row-unassigned"
             : (r.status === "Invalid Data" ? "row-error" : "");
      },
      columns: [
        { label: "S No", sort: "s_no", cls: "num mono",
          cell: function (r) { return esc(r.s_no); } },
        { label: "DWG No", sort: "dwg_no", cls: "mono strong",
          cell: function (r) { return esc(r.dwg_no); } },
        { label: "SAP No", sort: "sap_no", cls: "mono",
          cell: function (r) { return esc(r.sap_no); } },
        { label: "Description", sort: "description",
          cell: function (r) { return esc(r.description); } },
        { label: "ROP", sort: "rop", cls: "num mono strong",
          cell: function (r) { return num(r.rop, 2); } },
        /* The two primary inputs. Product Volume and the per-piece Weight are
           shown as reference only, in the cell tooltip. */
        { label: "Demand Product Volume (mm³)", sort: "demand_volume", cls: "num mono",
          title: function (r) {
            return "Product Volume " + num(r.product_volume) + " mm³ (reference only)";
          },
          cell: function (r) { return num(r.demand_volume); } },
        { label: "Demand Product Weight (Kg)", sort: "demand_weight", cls: "num mono",
          title: function (r) {
            return "Weight " + num(r.weight, 2) + " Kg per piece (reference only)";
          },
          cell: function (r) { return num(r.demand_weight, 2); } },
        { label: "Recommended Bin", sort: "recommended_bin",
          cell: function (r) {
            if (r.status === "Matched") return badge("ok", r.recommended_bin);
            if (r.status === "Invalid Data") return badge("warn", "No Suitable Bin");
            return badge("bad", "No Suitable Bin");
          } },
        { label: "Status", sort: "status",
          cell: function (r) {
            var kind = r.status === "Matched" ? "ok"
                     : (r.status === "Invalid Data" ? "warn" : "bad");
            return badge(kind, r.status);
          } },
        { label: "Volume Utilization %", sort: "volume_utilisation_pct", cls: "num",
          cell: function (r) { return utilBar(r.volume_utilisation_pct); } },
        { label: "Weight Utilization %", sort: "weight_utilisation_pct", cls: "num",
          cell: function (r) { return utilBar(r.weight_utilisation_pct); } },
        { label: "Recommendation Reason", cls: "reason-cell", headCls: "reason-col",
          title: function (r) { return r.reason; },
          cell: function (r) { return '<div class="clamp">' + esc(r.reason) + "</div>"; } }
      ]
    }
  };

  function view() { return VIEWS[state.mode] || VIEWS.master; }

  // --------------------------------------------------------------- alerts

  function showAlert(kind, message) {
    var box = el("alert");
    box.className = "alert " + kind;
    box.innerHTML = message;
    box.hidden = false;
  }

  function hideAlert() { el("alert").hidden = true; }

  function setLoading(on) {
    el("loading").hidden = !on;
    el("btn-sample").disabled = on;
  }

  // -------------------------------------------------------------- metrics

  function inputMetrics(s) {
    var matchedPct = s.total_products ? (s.matched_products / s.total_products) * 100 : 0;
    var avgVolume = s.average_volume_utilisation_pct;
    var avgWeight = s.average_weight_utilisation_pct;

    return [
      {
        label: "Total products",
        value: num(s.total_products),
        sub: s.rule_count + " bin categories in the rule master",
        cls: ""
      },
      {
        label: "Matched products",
        value: num(s.matched_products),
        sub: matchedPct.toFixed(1) + "% of products",
        cls: "is-ok"
      },
      {
        label: "No suitable bin",
        value: num(s.no_suitable_bin),
        sub: s.invalid_products + " row(s) with invalid data",
        cls: s.no_suitable_bin > 0 ? "is-bad" : "is-ok"
      },
      {
        label: "Total demand volume",
        value: compactVolume(s.total_demand_volume),
        sub: num(s.total_demand_volume) + " mm³",
        cls: ""
      },
      {
        label: "Total demand weight",
        value: num(s.total_demand_weight, 2) + " Kg",
        sub: (s.total_demand_weight / 1000).toFixed(3) + " tonne",
        cls: ""
      },
      {
        label: "Avg. utilisation (matched)",
        value: avgVolume === null || avgVolume === undefined ? "—" : avgVolume.toFixed(1) + "%",
        sub: "volume · weight " +
          (avgWeight === null || avgWeight === undefined ? "—" : avgWeight.toFixed(1) + "%"),
        cls: "is-warn"
      }
    ];
  }

  function masterMetrics(s) {
    var assignedPct = s.total_parts ? (s.assigned / s.total_parts) * 100 : 0;
    var avg = s.average_volume_utilisation;

    return [
      {
        label: "Total parts analysed",
        value: num(s.total_parts),
        sub: s.available_bins + " of " + s.total_bins + " bins available",
        cls: ""
      },
      {
        label: "Total required volume",
        value: compactVolume(s.total_required_volume),
        sub: num(s.total_required_volume) + " mm³",
        cls: ""
      },
      {
        label: "Total required weight",
        value: num(s.total_required_weight, 2) + " kg",
        sub: (s.total_required_weight / 1000).toFixed(3) + " tonne",
        cls: ""
      },
      {
        label: "Bins assigned",
        value: num(s.assigned),
        sub: assignedPct.toFixed(1) + "% of materials · " + s.distinct_bins_used + " distinct bins",
        cls: "is-ok"
      },
      {
        label: "Unassigned materials",
        value: num(s.unassigned),
        sub: s.no_suitable_bin + " no suitable bin · " + s.data_errors + " data errors",
        cls: s.unassigned > 0 ? "is-bad" : "is-ok"
      },
      {
        label: "Avg. volume utilisation",
        value: avg === null || avg === undefined ? "—" : (avg * 100).toFixed(1) + "%",
        sub: s.orientation_used_count + " assignment(s) needed rotation",
        cls: "is-warn"
      }
    ];
  }

  function renderMetrics() {
    var cards = state.mode === "input"
      ? inputMetrics(state.summary)
      : masterMetrics(state.summary);

    el("metrics").innerHTML = cards.map(function (c) {
      return '<div class="metric ' + c.cls + '">' +
        '<div class="metric-label">' + esc(c.label) + "</div>" +
        '<div class="metric-value">' + c.value + "</div>" +
        '<div class="metric-sub">' + esc(c.sub) + "</div></div>";
    }).join("");
  }

  // ---------------------------------------------------------- rule master

  function dropdownHtml(rule) {
    var isFirst = rule.priority === 0;
    var isLast = rule.priority === state.rules.length - 1;
    return '<div class="dropdown">' +
      '<button type="button" class="btn-icon" data-menu-trigger ' +
      'aria-label="Actions for ' + esc(rule.name) + '" title="Actions">&vellip;</button>' +
      '<div class="dropdown-menu" hidden>' +
      '<button type="button" data-rule-action="edit">Edit</button>' +
      '<button type="button" data-rule-action="move-up"' + (isFirst ? " disabled" : "") +
      '>Move up</button>' +
      '<button type="button" data-rule-action="move-down"' + (isLast ? " disabled" : "") +
      '>Move down</button>' +
      '<button type="button" data-rule-action="delete" class="danger">Delete</button>' +
      "</div></div>";
  }

  function renderRules() {
    var card = el("rules-card");
    if (state.mode !== "input") {
      card.hidden = true;
      return;
    }
    card.hidden = false;

    el("rules-card-body").hidden = state.rulesHidden;
    el("rules-toggle-label").textContent = state.rulesHidden
      ? "Show bin recommendations"
      : "Hide bin recommendations";

    var load = (state.summary && state.summary.bin_load) || {};
    el("rules-body").innerHTML = state.rules.map(function (rule) {
      var used = load[rule.name] || 0;
      return "<tr>" +
        '<td class="strong">' + badge("neutral", rule.name) + "</td>" +
        '<td class="num mono">' + num(rule.max_volume) + "</td>" +
        '<td class="num mono">' + num(rule.min_weight, 2) + "</td>" +
        '<td class="num mono">' + num(rule.max_weight, 2) + "</td>" +
        '<td class="num mono">' + (rule.priority + 1) + "</td>" +
        '<td class="num mono' + (used ? " strong" : " muted") + '">' + used + "</td>" +
        '<td class="rule-actions">' + dropdownHtml(rule) + "</td></tr>";
    }).join("");
  }

  // ------------------------------------------------------- rule CRUD api

  function ruleApi(url, options) {
    return fetch(url, options)
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data.ok) throw new Error(data.error || "Rule request failed.");
        return data;
      });
  }

  function loadRules() {
    return ruleApi("/api/rules")
      .then(function (data) {
        state.rules = data.rules || [];
        renderRules();
        renderToolbar();
      })
      .catch(function (error) {
        showAlert("error", "<strong>Could not load bin rules.</strong> " + esc(error.message));
      });
  }

  function refreshRules(message) {
    return loadRules().then(function () {
      if (message) showAlert("success", "<strong>" + message + "</strong>");
    });
  }

  // Re-run the last analysed workbook so the recommendations react
  // immediately to any rule change, without re-uploading the file.
  function autoReanalyse() {
    var results = el("results");
    if (results && !results.hidden) postAnalysis("/api/reanalyse", null);
  }

  function closeDropdowns() {
    Array.prototype.forEach.call(document.querySelectorAll(".dropdown-menu"), function (menu) {
      menu.hidden = true;
    });
  }

  function openRuleModal(rule) {
    el("rule-modal-title").textContent = rule ? "Edit bin rule" : "Add bin rule";
    el("rule-id").value = rule ? rule.id : "";
    el("rule-name").value = rule ? rule.name : "";
    el("rule-min-volume").value = rule ? rule.min_volume : "";
    el("rule-max-volume").value = rule ? rule.max_volume : "";
    el("rule-min-weight").value = rule ? rule.min_weight : "";
    el("rule-max-weight").value = rule ? rule.max_weight : "";
    el("rule-form-error").hidden = true;
    el("rule-modal").hidden = false;
    el("rule-name").focus();
  }

  function closeRuleModal() { el("rule-modal").hidden = true; }

  function saveRule() {
    var id = el("rule-id").value;
    var payload = {
      name: el("rule-name").value.trim(),
      min_volume: el("rule-min-volume").value,
      max_volume: el("rule-max-volume").value,
      min_weight: el("rule-min-weight").value,
      max_weight: el("rule-max-weight").value
    };
    var url = id ? "/api/rules/" + encodeURIComponent(id) : "/api/rules";
    var options = {
      method: id ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    };

    ruleApi(url, options)
      .then(function () {
        closeRuleModal();
        return refreshRules(id ? "Bin rule updated." : "Bin rule added.");
      })
      .then(autoReanalyse)
      .catch(function (error) {
        var box = el("rule-form-error");
        box.textContent = error.message;
        box.hidden = false;
      });
  }

  function deleteRule(rule) {
    if (!confirm('Delete bin rule "' + rule.name + '"?')) return;
    ruleApi("/api/rules/" + encodeURIComponent(rule.id), { method: "DELETE" })
      .then(function () {
        return refreshRules('Bin rule "' + rule.name + '" deleted.');
      })
      .then(autoReanalyse)
      .catch(function (error) {
        showAlert("error", "<strong>Could not delete rule.</strong> " + esc(error.message));
      });
  }

  function moveRule(rule, delta) {
    var ids = state.rules.map(function (r) { return r.id; });
    var index = ids.indexOf(rule.id);
    var swap = index + delta;
    if (swap < 0 || swap >= ids.length) return;
    ids[index] = ids[swap];
    ids[swap] = rule.id;

    ruleApi("/api/rules/reorder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: ids })
    })
      .then(function () { return refreshRules("Rule order updated."); })
      .then(autoReanalyse)
      .catch(function (error) {
        showAlert("error", "<strong>Could not reorder rules.</strong> " + esc(error.message));
      });
  }

  // --------------------------------------------------------------- filter

  function applyFilter() {
    var term = el("search").value.trim().toLowerCase();
    var status = el("filter-status").value;
    var binType = el("filter-bin").value;
    var fields = view().searchFields;

    state.filtered = state.rows.filter(function (r) {
      if (status && status !== "all" && r.status !== status) return false;
      if (binType && binType !== "all" && r.recommended_bin !== binType) return false;
      if (!term) return true;
      for (var i = 0; i < fields.length; i++) {
        var value = r[fields[i]];
        if (value !== null && value !== undefined &&
            String(value).toLowerCase().indexOf(term) !== -1) {
          return true;
        }
      }
      return false;
    });

    if (state.sortKey) {
      var key = state.sortKey, dir = state.sortDir;
      state.filtered.sort(function (a, b) {
        var x = a[key], y = b[key];
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
        return String(x).localeCompare(String(y)) * dir;
      });
    }

    state.page = 1;
    renderTable();
  }

  // -------------------------------------------------------- results table

  function renderHead() {
    var columns = view().columns;
    el("parts-head").innerHTML = "<tr>" + columns.map(function (c) {
      var cls = [];
      if (c.cls && c.cls.indexOf("num") === 0) cls.push("num");
      if (c.headCls) cls.push(c.headCls);
      if (c.sort) cls.push("sortable");
      var attrs = cls.length ? ' class="' + cls.join(" ") + '"' : "";
      if (c.sort) attrs += ' data-sort="' + c.sort + '"';
      return "<th" + attrs + ">" + c.label + "</th>";
    }).join("") + "</tr>";
  }

  function pageSlice() {
    if (state.pageSize === 0) return state.filtered;
    var start = (state.page - 1) * state.pageSize;
    return state.filtered.slice(start, start + state.pageSize);
  }

  function renderTable() {
    var rows = pageSlice();
    var columns = view().columns;
    var rowClass = view().rowClass;
    var body = el("parts-body");

    if (!rows.length) {
      body.innerHTML = '<tr class="empty-row"><td colspan="' + columns.length +
        '">No materials match the current filter.</td></tr>';
    } else {
      body.innerHTML = rows.map(function (r) {
        var cells = columns.map(function (c) {
          var attrs = c.cls ? ' class="' + c.cls + '"' : "";
          if (c.title) attrs += ' title="' + esc(c.title(r)) + '"';
          return "<td" + attrs + ">" + c.cell(r) + "</td>";
        }).join("");
        return '<tr class="' + rowClass(r) + '">' + cells + "</tr>";
      }).join("");
    }

    el("row-count").textContent =
      "Showing " + rows.length + " of " + state.filtered.length +
      " material(s) · " + state.rows.length + " total";

    renderPager();
  }

  function renderPager() {
    var pager = el("pager");
    if (state.pageSize === 0 || state.filtered.length <= state.pageSize) {
      pager.innerHTML = "";
      return;
    }

    var totalPages = Math.ceil(state.filtered.length / state.pageSize);
    var html = '<button class="btn btn-sm" data-page="' + (state.page - 1) + '"' +
               (state.page === 1 ? " disabled" : "") + ">Previous</button>";

    var start = Math.max(1, state.page - 2);
    var end = Math.min(totalPages, start + 4);
    start = Math.max(1, end - 4);

    for (var i = start; i <= end; i++) {
      html += '<button class="btn btn-sm' + (i === state.page ? " is-active" : "") +
              '" data-page="' + i + '">' + i + "</button>";
    }

    html += '<button class="btn btn-sm" data-page="' + (state.page + 1) + '"' +
            (state.page === totalPages ? " disabled" : "") + ">Next</button>" +
            '<span class="spacer"></span><span class="muted">Page ' + state.page +
            " of " + totalPages + "</span>";

    pager.innerHTML = html;
  }

  // ----------------------------------------------------------- bins table

  function renderBins() {
    var tab = el("tab-bins");
    if (state.mode === "input") {
      tab.hidden = true;
      return;
    }
    tab.hidden = false;

    var load = (state.summary && state.summary.bin_load) || {};
    el("tab-bins-count").textContent = state.bins.length;

    el("bins-body").innerHTML = state.bins.map(function (b) {
      var statusBadge = b.is_available
        ? badge("ok", b.status || "Available")
        : badge("neutral", b.status || "Unavailable");
      var used = load[b.bin_id] || 0;
      return "<tr>" +
        '<td class="mono strong">' + esc(b.bin_id) + "</td>" +
        "<td>" + esc(b.description) + "</td>" +
        '<td class="num mono">' + dims(b.length, b.breadth, b.width) + "</td>" +
        '<td class="num mono">' + num(b.cubic_capacity) + "</td>" +
        '<td class="num mono">' + num(b.max_weight, 2) + "</td>" +
        "<td>" + esc(b.location) + "</td>" +
        "<td>" + statusBadge + "</td>" +
        '<td class="num mono' + (used ? " strong" : " muted") + '">' + used + "</td></tr>";
    }).join("");
  }

  // --------------------------------------------------------- issues table

  function renderIssues() {
    var pill = el("tab-issues-count");
    pill.textContent = state.issues.length;
    pill.className = "pill" + (state.issues.some(function (i) { return i.severity === "error"; }) ? " is-bad" : "");

    if (!state.issues.length) {
      el("issues-body").innerHTML =
        '<tr class="empty-row"><td colspan="5">No validation issues — the workbook was read cleanly.</td></tr>';
      return;
    }

    el("issues-body").innerHTML = state.issues.map(function (i) {
      var mark = i.severity === "error" ? badge("bad", "Error") : badge("warn", "Warning");
      return "<tr>" +
        "<td>" + mark + "</td>" +
        "<td>" + esc(i.sheet) + "</td>" +
        '<td class="num mono">' + (i.row === null ? "—" : i.row) + "</td>" +
        '<td class="mono">' + esc(i.reference) + "</td>" +
        "<td>" + esc(i.message) + "</td></tr>";
    }).join("");
  }

  // ---------------------------------------------------------- guide table

  function renderGuide() {
    var head = el("guide-head");
    var body = el("guide-body");

    el("tab-guide").hidden = state.mode === "input" && !state.guide.length;

    if (!state.guide.length) {
      head.innerHTML = "";
      body.innerHTML =
        '<tr class="empty-row"><td>The uploaded workbook has no Logic Guide sheet.</td></tr>';
      return;
    }

    head.innerHTML = "<tr>" + state.guide[0].map(function (c) {
      return "<th>" + esc(c) + "</th>";
    }).join("") + "</tr>";

    body.innerHTML = state.guide.slice(1).map(function (row) {
      return "<tr>" + row.map(function (c, index) {
        return "<td" + (index === 0 ? ' class="strong"' : "") + ">" + esc(c) + "</td>";
      }).join("") + "</tr>";
    }).join("");
  }

  // -------------------------------------------------------------- toolbar

  function renderToolbar() {
    var current = view();

    el("search").placeholder = current.searchPlaceholder;
    el("search").value = "";

    el("filter-status").innerHTML = current.statuses.map(function (pair) {
      return '<option value="' + esc(pair[0]) + '">' + esc(pair[1]) + "</option>";
    }).join("");

    var binFilter = el("filter-bin");
    if (state.mode === "input" && state.rules.length) {
      binFilter.hidden = false;
      binFilter.innerHTML = '<option value="all">All bin types</option>' +
        state.rules.map(function (rule) {
          return '<option value="' + esc(rule.name) + '">' + esc(rule.name) + "</option>";
        }).join("") +
        '<option value="No Suitable Bin">No Suitable Bin</option>';
    } else {
      binFilter.hidden = true;
      binFilter.innerHTML = '<option value="all">All bin types</option>';
    }
  }

  // -------------------------------------------------------------- results

  function renderFooter(data) {
    if (state.mode === "input") {
      el("engine-formula").innerHTML =
        "Volume Utilization % = Demand Volume &divide; Bin Max Volume" +
        " &nbsp;&middot;&nbsp; Weight Utilization % = Demand Weight &divide; Bin Max Weight";
      el("engine-settings").textContent =
        data.settings.rule_count + " bin rules · ties broken by " +
        (data.settings.prefer_priority_over_size ? "declared priority" : "smallest suitable category");
    } else {
      el("engine-formula").innerHTML =
        "Total Volume = L &times; B &times; W &nbsp;&middot;&nbsp; Required Volume = Total Volume &times; ROP" +
        " &nbsp;&middot;&nbsp; Required Weight = Weight/Unit &times; ROP";
      el("engine-settings").textContent =
        "Orientation " + (data.settings.allow_orientation ? "enabled" : "disabled") +
        " · usable volume " + (data.settings.volume_utilisation_factor * 100).toFixed(0) + "%" +
        " · usable weight " + (data.settings.weight_utilisation_factor * 100).toFixed(0) + "%";
    }
  }

  function renderSummaryAlert() {
    var s = state.summary;

    if (state.mode === "input") {
      var unplaced = s.no_suitable_bin + s.invalid_products;
      if (unplaced > 0) {
        showAlert("warning",
          "<strong>" + s.matched_products + " of " + s.total_products +
          " products matched a bin category.</strong> " + s.no_suitable_bin +
          " had no suitable bin" +
          (s.invalid_products ? " and " + s.invalid_products + " had invalid demand data" : "") +
          " — see the highlighted rows for the limiting factor.");
      } else {
        showAlert("success",
          "<strong>All " + s.total_products + " products matched a bin category.</strong>");
      }
      return;
    }

    if (s.unassigned > 0) {
      showAlert("warning",
        "<strong>" + s.assigned + " of " + s.total_parts + " materials assigned.</strong> " +
        s.unassigned + " could not be placed — see the highlighted rows for the limiting factor.");
    } else {
      showAlert("success",
        "<strong>All " + s.total_parts + " materials were assigned a bin.</strong> " +
        "Average volume utilisation " +
        (s.average_volume_utilisation === null ? "n/a" : (s.average_volume_utilisation * 100).toFixed(1) + "%") + ".");
    }
  }

  function renderAll(data) {
    state.mode = data.mode === "input" ? "input" : "master";
    state.rows = data.rows || [];
    state.bins = data.bins || [];
    state.issues = data.issues || [];
    state.guide = data.guide || [];
    state.summary = data.summary;
    state.download = data.download;
    state.sortKey = null;
    state.sortDir = 1;
    state.rulesHidden = false;

    /* Rules come from the persistent store so every edit is reflected even
       before the workbook is re-analysed; the payload copy is only a fallback
       while the fetch completes. */
    state.rules = data.rules || [];

    renderMetrics();
    renderRules();
    renderToolbar();
    renderHead();
    renderBins();
    renderIssues();
    renderGuide();
    applyFilter();

    // The materials tab is the only one guaranteed to exist in both modes.
    selectTab("parts");

    el("results").hidden = false;
    el("btn-download").disabled = false;

    renderFooter(data);
    renderSummaryAlert();

    if (state.mode === "input") loadRules();

    // Keep the results on screen so the user always sees them straight away.
    var res = el("results");
    if (res && res.getBoundingClientRect().bottom > window.innerHeight) {
      res.scrollIntoView({ block: "start" });
    }
  }

  // -------------------------------------------------------------- network

  function postAnalysis(url, formData) {
    hideAlert();
    setLoading(true);

    fetch(url, formData ? { method: "POST", body: formData } : { method: "POST" })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (result) {
        setLoading(false);
        if (!result.ok || !result.data.ok) {
          showAlert("error", "<strong>Could not analyse the workbook.</strong> " +
            esc(result.data.error || "Unknown error.") +
            " <span class='alert-hint'>The file needs an \"Input\" sheet carrying the demand volume and weight, or a \"Part Requirements\" sheet with a \"Master\" bin sheet.</span>");
          return;
        }
        renderAll(result.data);
      })
      .catch(function (error) {
        setLoading(false);
        showAlert("error", "<strong>Request failed.</strong> " + esc(error.message));
      });
  }

  function analyseFile(file) {
    if (!file) return;
    if (!/\.(xlsx|xlsm)$/i.test(file.name)) {
      showAlert("error", "Please choose an .xlsx or .xlsm workbook.");
      return;
    }
    el("file-name").textContent = file.name;
    el("file-chip").hidden = false;

    var form = new FormData();
    form.append("file", file);
    postAnalysis("/api/analyse", form);
  }

  // --------------------------------------------------------------- events

  function selectTab(name) {
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
      t.classList.toggle("is-active", t.getAttribute("data-tab") === name);
    });
    Array.prototype.forEach.call(document.querySelectorAll(".tab-panel"), function (panel) {
      panel.classList.toggle("is-active", panel.getAttribute("data-panel") === name);
    });
  }

  function bind() {
    var dropzone = el("dropzone");
    var input = el("file-input");

    dropzone.addEventListener("click", function () { input.click(); });
    dropzone.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
    });

    ["dragenter", "dragover"].forEach(function (name) {
      dropzone.addEventListener(name, function (e) {
        e.preventDefault(); e.stopPropagation();
        dropzone.classList.add("is-over");
      });
    });

    ["dragleave", "drop"].forEach(function (name) {
      dropzone.addEventListener(name, function (e) {
        e.preventDefault(); e.stopPropagation();
        dropzone.classList.remove("is-over");
      });
    });

    dropzone.addEventListener("drop", function (e) {
      if (e.dataTransfer.files && e.dataTransfer.files.length) {
        analyseFile(e.dataTransfer.files[0]);
      }
    });

    input.addEventListener("change", function () {
      if (input.files.length) analyseFile(input.files[0]);
    });

    el("btn-clear").addEventListener("click", function (e) {
      e.stopPropagation();
      input.value = "";
      el("file-chip").hidden = true;
      el("results").hidden = true;
      el("btn-download").disabled = true;
      hideAlert();
    });

    el("btn-sample").addEventListener("click", function () {
      el("file-name").textContent = "Sample workbook";
      el("file-chip").hidden = false;
      postAnalysis("/api/sample", null);
    });

    el("btn-download").addEventListener("click", function () {
      if (state.download) window.location.href = "/api/download/" + state.download.token;
    });

    el("search").addEventListener("input", applyFilter);
    el("filter-status").addEventListener("change", applyFilter);
    el("filter-bin").addEventListener("change", applyFilter);

    el("page-size").addEventListener("change", function () {
      state.pageSize = parseInt(this.value, 10);
      state.page = 1;
      renderTable();
    });

    el("pager").addEventListener("click", function (e) {
      var button = e.target.closest("button[data-page]");
      if (!button || button.disabled) return;
      state.page = parseInt(button.getAttribute("data-page"), 10);
      renderTable();
      document.querySelector(".table-scroll").scrollTop = 0;
    });

    /* The header is rebuilt per mode, so sorting is delegated to the thead. */
    el("parts-head").addEventListener("click", function (e) {
      var th = e.target.closest("th[data-sort]");
      if (!th) return;
      var key = th.getAttribute("data-sort");
      state.sortDir = state.sortKey === key ? -state.sortDir : 1;
      state.sortKey = key;

      Array.prototype.forEach.call(el("parts-head").querySelectorAll("th"), function (other) {
        other.classList.remove("sort-asc", "sort-desc");
      });
      th.classList.add(state.sortDir === 1 ? "sort-asc" : "sort-desc");

      applyFilter();
    });

    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (tab) {
      tab.addEventListener("click", function () {
        selectTab(tab.getAttribute("data-tab"));
      });
    });

    // ---------------------------------------------------- rule CRUD events

    /* Dropping or browsing any Excel anywhere on the page starts the analysis
       immediately - no extra button is needed. */
    document.addEventListener("dragover", function (e) { e.preventDefault(); });

    document.addEventListener("drop", function (e) {
      e.preventDefault();
      var file = e.dataTransfer.files && e.dataTransfer.files[0];
      if (file) analyseFile(file);
    });

    el("rule-modal-close").addEventListener("click", closeRuleModal);
    el("rule-form-cancel").addEventListener("click", closeRuleModal);

    el("rule-form").addEventListener("submit", function (e) {
      e.preventDefault();
      saveRule();
    });

    el("rule-modal").addEventListener("click", function (e) {
      if (e.target === this) closeRuleModal();
    });

    /* The dropdown menus and their actions are delegated to the document so
       rows rebuilt by renderRules() keep working without re-binding. */
    document.addEventListener("click", function (e) {
      var trigger = e.target.closest("[data-menu-trigger]");
      if (trigger) {
        e.stopPropagation();
        var menu = trigger.closest(".dropdown").querySelector(".dropdown-menu");
        var opening = menu.hidden;
        closeDropdowns();
        if (!opening) return;

        // Positioned against the viewport (fixed) so a small table cannot clip
        // the menu; flip it upward when it would run off the bottom edge.
        menu.hidden = false;
        var rect = trigger.getBoundingClientRect();
        var width = menu.offsetWidth;
        var height = menu.offsetHeight;
        menu.style.left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)) + "px";
        var top = rect.bottom + 4;
        if (top + height > window.innerHeight - 8) top = rect.top - height - 4;
        menu.style.top = Math.max(8, top) + "px";
        return;
      }

      var action = e.target.closest("[data-rule-action]");
      if (action) {
        e.stopPropagation();
        var row = action.closest("tr");
        var index = Array.prototype.indexOf.call(el("rules-body").children, row);
        var rule = state.rules[index];
        var kind = action.getAttribute("data-rule-action");
        closeDropdowns();
        if (kind === "edit") openRuleModal(rule);
        else if (kind === "delete") deleteRule(rule);
        else if (kind === "move-up") moveRule(rule, -1);
        else if (kind === "move-down") moveRule(rule, 1);
        return;
      }

      var headerAction = e.target.closest("[data-rules-action]");
      if (headerAction) {
        e.stopPropagation();
        closeDropdowns();
        var headerKind = headerAction.getAttribute("data-rules-action");
        if (headerKind === "add") openRuleModal(null);
        else if (headerKind === "rerun") postAnalysis("/api/reanalyse", null);
        else if (headerKind === "toggle") {
          state.rulesHidden = !state.rulesHidden;
          renderRules();
        }
        return;
      }

      closeDropdowns();
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        closeRuleModal();
        closeDropdowns();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", bind);
})();
