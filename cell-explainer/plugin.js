/**
 * Cell Explainer - Arc Plugin
 *
 * AI-powered explanation for any TM1 cell.
 *
 * Architecture:
 *   1. PAGE plugin under Tools menu = main UI (paste Cell Reference, analyze, view results)
 *   2. MENU plugin on cube objects = right-click cube → opens page with cube name pre-filled
 *
 * Cell-level right-click hook (e.g. "menu/cell") is NOT supported by Arc as of v5.1.
 * The workflow is: right-click cube → open plugin → paste Cell Reference → analyze.
 */


// ============================================================================
// 0. CELL REFERENCE DIALOG INJECTOR
// Watches for Arc's ngDialog (Cell Reference) to open, then injects a
// "Send to AI Analyst" button. Retries up to 10x / 1s to handle Angular
// rendering delay (table may not exist yet when ngdialog-content is added).
// ============================================================================
arc.run(['$rootScope', 'Notification', function ($rootScope, Notification) {

    function tryInject(dialog, attempt) {
        if (dialog.querySelector('.ai-send-btn')) return;

        var table = dialog.querySelector('.table.table-sm.table-middle');
        if (!table) {
            if ((attempt || 0) < 10) {
                setTimeout(function () { tryInject(dialog, (attempt || 0) + 1); }, 100);
            }
            return;
        }

        var btn = document.createElement('button');
        btn.className = 'btn btn-success btn-block btn-sm ai-send-btn';
        btn.style.marginTop = '10px';
        btn.innerHTML = '<i class="fa fa-paper-plane"></i> Send to AI Analyst';

        btn.addEventListener('click', function () {
            var label = dialog.querySelector('.modal-body label.ng-binding');
            var valueLine = label ? label.innerText.trim() : '';
            var raw = valueLine ? valueLine + '\n' + table.innerText : table.innerText;

            var hashMatch = window.location.hash.match(/#\/cube\/([^\/]+)\/([^\/]+)/);
            var cubeName  = hashMatch ? decodeURIComponent(hashMatch[2]) : '';
            var instName  = hashMatch ? decodeURIComponent(hashMatch[1]) : '';

            $rootScope.cellExplainerPendingCell = {
                raw: raw, cube: cubeName, instance: instName
            };
            $rootScope.$broadcast('cellExplainer:openWithCell', $rootScope.cellExplainerPendingCell);

            // Close the ngDialog
            try {
                var ngDlg = angular.element(document.body).injector().get('ngDialog');
                if (ngDlg) { ngDlg.closeAll(); }
            } catch (e) {
                var closeBtn = dialog.querySelector('.ngdialog-close');
                if (closeBtn) { closeBtn.click(); }
            }

            window.location.hash = '#/cell-explainer/' + (instName || '');
        });

        var body = dialog.querySelector('.modal-body');
        (body || dialog).appendChild(btn);
    }

    var observer = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            m.addedNodes.forEach(function (node) {
                if (!node || node.nodeType !== 1) return;
                if (node.classList && node.classList.contains('ngdialog-content')) {
                    tryInject(node, 0);
                } else if (node.querySelector) {
                    var inner = node.querySelector('.ngdialog-content');
                    if (inner) tryInject(inner, 0);
                }
            });
        });
    });

    observer.observe(document.body, { childList: true, subtree: true });
}]);


// ============================================================================
// 1. PAGE PLUGIN - registers "Cell Explainer" under Tools menu
// ============================================================================
arc.run(['$rootScope', function ($rootScope) {
    $rootScope.plugin("cellExplainer", "Cell Explainer", "page", {
        menu: "tools",
        icon: "fa-search-plus",
        description: "AI-powered cell explanation: transactions and root cause",
        author: "Alice Tan",
        version: "0.1.0"
    });
}]);


// ============================================================================
// 2. MENU PLUGIN on cube objects - "Explain a cell with AI"
//    User right-clicks a cube in the left tree, picks this action,
//    and we open the page plugin with the cube name pre-populated.
// ============================================================================
arc.run(['$rootScope', function ($rootScope) {
    $rootScope.plugin("cellExplainerFromCube", "Explain Cube with AI", "menu/cube", {
        icon: "fa-magic",
        description: "Open Cell Explainer pre-filled with this cube",
        author: "Alice Tan",
        version: "0.1.0"
        // OPTIONAL: restrict this menu item to a specific cube / instance:
        //   instanceName: "FreshMart_PROD",
        //   objectName:   "Consol_Reporting"
        // Useful for client-specific demos where you want the AI option
        // to appear ONLY on certain cubes (avoids menu pollution).
    });
}]);


// Service backing the menu plugin. Service name MUST match the menu plugin id.
arc.service('cellExplainerFromCube', ['$rootScope', 'Notification',
    function ($rootScope, Notification) {
        this.execute = function (instance, name, branch) {
            $rootScope.cellExplainerPending = {
                instance: instance,
                cube: name,
                triggeredAt: Date.now()
            };
            $rootScope.$broadcast("cellExplainer.openWithCube", { instance: instance, cube: name });
            Notification.info({
                title: "<i class='fa fa-magic'></i> Cell Explainer",
                message: "Opening with cube <strong>" + name + "</strong>. " +
                         "If the page doesn't open automatically, click Tools → Cell Explainer."
            });
            window.location.hash = '#/cell-explainer/' + (instance || '');
        };
    }
]);


// ============================================================================
// 3. PAGE DIRECTIVE - the main UI. Directive name MUST match plugin id.
// ============================================================================
arc.directive("cellExplainer", function () {
    return {
        restrict: "EA",
        replace: true,
        scope: {
            instance: "=tm1Instance"
        },
        templateUrl: "__/plugins/cell-explainer/template.html",

        controller: ["$scope", "$rootScope", "$tm1", "$http", "$translate", "$timeout", "Notification",
            function ($scope, $rootScope, $tm1, $http, $translate, $timeout, Notification) {

                // ============ CONFIG ============
                var BACKEND_URL = "http://localhost:8000";

                // ============ DATE HELPERS (tx range) ============
                function dateOffset(days) {
                    var d = new Date();
                    d.setDate(d.getDate() + days);
                    d.setHours(0, 0, 0, 0);
                    return d;
                }

                function dateToIso(value) {
                    if (!value) return null;
                    var d = value instanceof Date ? value : new Date(value);
                    if (isNaN(d.getTime())) return null;
                    return d.toISOString().slice(0, 10);
                }

                function applyTxPreset(preset) {
                    var days = parseInt(preset, 10);
                    if (!isNaN(days)) {
                        $scope.state.txStart = dateOffset(-days);
                        $scope.state.txEnd   = dateOffset(0);
                        updateTxEndMin();
                    }
                }

                function updateTxEndMin() {
                    $scope.txEndMin = dateToIso($scope.state.txStart);
                    var start = $scope.state.txStart instanceof Date ? $scope.state.txStart : new Date($scope.state.txStart);
                    var end = $scope.state.txEnd instanceof Date ? $scope.state.txEnd : new Date($scope.state.txEnd);
                    if (!isNaN(start.getTime()) && (!end || isNaN(end.getTime()) || end < start)) {
                        $scope.state.txEnd = new Date(start.getTime());
                    }
                }

                // ============ STATE ============
                $scope.state = {
                    step: "paste",
                    cube: "",
                    tuple: [],
                    value: null,
                    action: null,
                    txRangePreset: "30",
                    txStart: dateOffset(-30),
                    txEnd:   dateOffset(0),
                    txMaxPages: 3,
                    result: null,
                    error: null
                };
                $scope.txRangePresets = [
                    { label: "Last 30 days",  value: "30"     },
                    { label: "Last 90 days",  value: "90"     },
                    { label: "Last 180 days", value: "180"    },
                    { label: "Custom",        value: "custom" }
                ];
                $scope.applyTxPreset = applyTxPreset;
                $scope.onTxStartChanged = function () {
                    $scope.state.txRangePreset = "custom";
                    updateTxEndMin();
                };
                $scope.onTxEndChanged = function () {
                    $scope.state.txRangePreset = "custom";
                    updateTxEndMin();
                };
                updateTxEndMin();

                $scope.txLimit = 5;
                $scope.showRaw = false;
                $scope.actionBtns = [
                    { action: "tx_history",       label: "Transactions", icon: "fa-history"       },
                    { action: "root_cause",        label: "Value Drivers", icon: "fa-search"       }
                ];
                $scope.followUpQuestion = "";
                $scope.cubeInfo = null;
                $scope.scenarios = [];
                $scope.compare = { left: "", right: "", varianceRaw: null, variancePct: null };

                $scope.loadScenarios = function (callback) {
                    if (!$scope.state.cube) {
                        if (callback) callback();
                        return;
                    }
                    $http.get(BACKEND_URL + "/api/cube-scenarios", {
                        params: { cube: $scope.state.cube }
                    }).then(function (res) {
                        $scope.scenarios = (res.data && res.data.elements) || [];
                        if (callback) callback();
                    }, function () {
                        $scope.scenarios = [];
                        if (callback) callback();
                    });
                };

                $scope._updateCompareVariance = function () {
                    var raw = $scope.state.result && $scope.state.result.scenario_raw_values;
                    if (!raw) return;
                    var lv = raw[$scope.compare.left];
                    var rv = raw[$scope.compare.right];
                    if (lv !== undefined && rv !== undefined) {
                        $scope.compare.varianceRaw = lv - rv;
                        $scope.compare.variancePct = rv ? ((lv - rv) / Math.abs(rv) * 100) : null;
                    } else {
                        $scope.compare.varianceRaw = null;
                        $scope.compare.variancePct = null;
                    }
                };

                $scope.fmtNum = function (n, signed) {
                    if (n === null || n === undefined || isNaN(n)) return "—";
                    var sign = (signed && n > 0) ? "+" : "";
                    return sign + Math.round(n).toLocaleString("en-US");
                };

                $scope.fmtPct = function (pct) {
                    if (pct === null || pct === undefined) return "";
                    return (pct >= 0 ? "+" : "") + pct.toFixed(1) + "%";
                };

                $scope.loadCubeInfo = function () {
                    if (!$scope.state.cube) return;
                    $scope.cubeInfo = null;
                    $http.get(BACKEND_URL + "/api/cube-info", {
                        params: { cube: $scope.state.cube }
                    }).then(function (res) {
                        $scope.cubeInfo = res.data;
                        $scope.loadScenarios();
                    }, function () {
                        $scope.cubeInfo = { dimensions: [], transactions: [] };
                    });
                };

                // ============ PICK UP PRE-FILL ON INIT ============
                // Case 1: came from menu/cube right-click (cube name only)
                if ($rootScope.cellExplainerPending &&
                    $rootScope.cellExplainerPending.instance === $scope.instance) {
                    $scope.state.cube = $rootScope.cellExplainerPending.cube;
                    $scope.state.step = "cube_mode";
                    delete $rootScope.cellExplainerPending;
                    $scope.loadCubeInfo();
                }

                // Case 2: came from "Send to AI" with full cell context → auto-analyze
                // $timeout defers until after all $scope.xxx assignments are done
                $timeout(function () {
                    var pending = $rootScope.cellExplainerPendingCell;
                    if (!pending) return;
                    if (pending.instance && pending.instance !== $scope.instance) return;
                    delete $rootScope.cellExplainerPendingCell;

                    if (pending.tuple && pending.tuple.length) {
                        $scope.state.cube  = pending.cube || $scope.state.cube;
                        $scope.state.tuple = pending.tuple;
                        $scope.state.value = pending.value || null;
                        $scope.state.step  = "review";
                        $scope.loadScenarios(function () { $scope.analyze("root_cause"); });
                    } else if (pending.raw) {
                        var parsed = parseCellReference(pending.raw);
                        if (parsed.tuple && parsed.tuple.length) {
                            $scope.state.cube  = pending.cube || $scope.state.cube;
                            $scope.state.tuple = parsed.tuple;
                            $scope.state.value = parsed.value;
                            $scope.state.step  = "review";
                            $scope.loadScenarios(function () { $scope.analyze("root_cause"); });
                        }
                    }
                }, 0);

                // Listen for cube-only pre-fill (from menu/cube right-click, tab already open)
                $scope.$on("cellExplainer.openWithCube", function (event, args) {
                    if (args.instance === $scope.instance) {
                        $scope.state.cube = args.cube;
                        $scope.state.step = "cube_mode";
                        $scope.state.result = null;
                        $scope.state.error = null;
                        $scope.loadCubeInfo();
                    }
                });

                // Listen for full cell context (from "Send to AI" button or injected dialog btn)
                $scope.$on("cellExplainer:openWithCell", function (event, args) {
                    if (args.instance !== $scope.instance) return;

                    if (args.tuple && args.tuple.length) {
                        // Full structured context — pre-fill and auto-analyze
                        $scope.state.cube  = args.cube || $scope.state.cube;
                        $scope.state.tuple = args.tuple;
                        $scope.state.value = args.value || null;
                        $scope.state.step  = "review";
                        $scope.state.error = null;
                        $scope.loadScenarios(function () { $scope.analyze("root_cause"); });
                    } else if (args.raw) {
                        // Raw text from DOM injection — parse then auto-analyze
                        var parsed = parseCellReference(args.raw);
                        if (parsed.tuple && parsed.tuple.length) {
                            $scope.state.cube  = args.cube || $scope.state.cube;
                            $scope.state.tuple = parsed.tuple;
                            $scope.state.value = parsed.value;
                            $scope.state.step  = "review";
                            $scope.state.error = null;
                            $scope.loadScenarios(function () { $scope.analyze("root_cause"); });
                        }
                    }
                });

                // ============ PASTE HANDLING ============
                $scope.onPaste = function (event) {
                    var ev = event.originalEvent || event;
                    if (!ev.clipboardData) return;

                    var text = ev.clipboardData.getData("text/plain");
                    var parsed = parseCellReference(text);

                    if (!parsed.tuple || parsed.tuple.length === 0) {
                        $scope.state.error = "Could not detect a cell reference. " +
                            "In Arc: right-click a cell → Cell Reference → select all → Ctrl+C, then paste here.";
                        return;
                    }

                    $scope.state.tuple = parsed.tuple;
                    $scope.state.value = parsed.value;
                    $scope.state.step = "review";
                    $scope.state.error = null;
                    ev.preventDefault();
                };

                function parseCellReference(text) {
                    var lines = text.split(/\r?\n/).map(function (l) { return l.trim(); }).filter(Boolean);
                    var value = null;
                    var tuple = [];
                    var i = 0;

                    if (lines[i] && /^Value:/i.test(lines[i])) {
                        value = lines[i].replace(/^Value:\s*/i, "").trim();
                        i++;
                    }
                    if (lines[i] && /Dimension/i.test(lines[i]) && /Element/i.test(lines[i])) {
                        i++;
                    }
                    for (; i < lines.length; i++) {
                        var parts = lines[i].split(/\t|\s{2,}/).map(function (s) { return s.trim(); }).filter(Boolean);
                        if (parts.length >= 3) {
                            tuple.push({
                                dimension: parts[0],
                                hierarchy: parts[1],
                                element: parts.slice(2).join(" ")
                            });
                        }
                    }
                    return { value: value, tuple: tuple };
                }

                // ============ ANALYZE ============
                $scope.analyze = function (action) {
                    if (action === "tx_history" && $scope.state.txRangePreset !== "custom") {
                        applyTxPreset($scope.state.txRangePreset);
                    }
                    if (!$scope.state.cube) {
                        $scope.state.error = "Please enter the cube name.";
                        return;
                    }
                    $scope.state.action = action;
                    $scope.state.step = "analyzing";
                    $scope.state.error = null;
                    $scope.showRaw = false;
                    $scope.txLimit = 5;

                    var payload = {
                        instance: $scope.instance,
                        cube: $scope.state.cube,
                        tuple: $scope.state.tuple,
                        value: $scope.state.value,
                        action: action
                    };
                    if (action === "root_cause") {
                        payload.scenarios = $scope.scenarios || [];
                    }
                    if (action === "tx_history") {
                        payload.tx_start    = dateToIso($scope.state.txStart);
                        payload.tx_end      = dateToIso($scope.state.txEnd);
                        payload.tx_max_pages = $scope.state.txMaxPages || 3;
                    }

                    $http.post(BACKEND_URL + "/api/explain-cell", payload, { timeout: 60000 })
                        .then(function (response) {
                            $scope.state.result = response.data;
                            $scope.state.step = "result";
                            var d = response.data;
                            if (d.scenario_raw_values) {
                                var current = d.current_scenario || "";
                                var currentMatch = $scope.scenarios.find(function (s) {
                                    return String(s).toLowerCase() === String(current).toLowerCase();
                                });
                                var budMatch = $scope.scenarios.find(function (s) {
                                    return /^bud(get)?$/i.test(String(s));
                                });
                                $scope.compare.left = currentMatch || $scope.scenarios[0] || "";
                                $scope.compare.right = budMatch || $scope.scenarios[1] || $scope.compare.left;
                                $scope._updateCompareVariance();
                            }
                        }, function (error) {
                            var msg = (error && error.statusText) || error.status || "unreachable";
                            $scope.state.error = "Backend error: " + msg +
                                " (is FastAPI running on " + BACKEND_URL + "?)";
                            $scope.state.step = "review";
                            Notification.error({
                                title: "<i class='fa fa-exclamation-triangle'></i> Cell Explainer",
                                message: msg
                            });
                        });
                };

                $scope.doFollowUp = function () {
                    if (!$scope.followUpQuestion) return;
                    var q = $scope.followUpQuestion;
                    $scope.followUpQuestion = "";
                    $scope.followUp(q);
                };

                $scope.followUp = function (question) {
                    $scope.state.step = "analyzing";
                    $http.post(BACKEND_URL + "/api/explain-cell", {
                        instance: $scope.instance,
                        cube: $scope.state.cube,
                        tuple: $scope.state.tuple,
                        value: $scope.state.value,
                        action: "followup",
                        question: question
                    }).then(function (response) {
                        $scope.state.result = response.data;
                        $scope.state.step = "result";
                    });
                };

                $scope.drillDriver = function (driver) {
                    var matched = false;
                    $scope.state.tuple = $scope.state.tuple.map(function (t) {
                        if (t.dimension === driver.dimension) {
                            matched = true;
                            return angular.extend({}, t, { element: driver.name });
                        }
                        return t;
                    });
                    if (matched) {
                        $scope.analyze($scope.state.action || "root_cause");
                    }
                };

                $scope.reset = function () {
                    $scope.state.step = "paste";
                    $scope.state.tuple = [];
                    $scope.state.value = null;
                    $scope.state.action = null;
                    $scope.state.result = null;
                    $scope.state.error = null;
                };

                $scope.sendToAnalyst = function () {
                    if (!$scope.state.cube) return;

                    // Store context on $rootScope so the page can pick it up on init
                    // (broadcast alone fails if the tab isn't open yet)
                    $rootScope.cellExplainerPendingCell = {
                        instance: $scope.instance,
                        cube: $scope.state.cube,
                        tuple: $scope.state.tuple,
                        value: $scope.state.value
                    };

                    // Also broadcast for the case where the tab is already open
                    $rootScope.$broadcast('cellExplainer:openWithCell', $rootScope.cellExplainerPendingCell);

                    window.location.hash = '#/cell-explainer/' + ($scope.instance || '');
                };

                // ============ HEALTH CHECK ============
                $scope.testTm1Connection = function () {
                    $tm1.async($scope.instance, "GET", "/Configuration/ProductVersion/$value", null)
                        .then(function (result) {
                            Notification.success({
                                title: "<i class='fa fa-check'></i> TM1 OK",
                                message: "Version: " + result.data
                            });
                        });
                };

                // ============ LIFECYCLE ============
                $scope.$on("close-tab", function (event, args) {
                    if (args.page === "cellExplainer" &&
                        args.instance === $scope.instance &&
                        args.name == null) {
                        $rootScope.close(args.page, { instance: $scope.instance });
                    }
                });

                $scope.$on("login-reload", function (event, args) {
                    if (args.instance === $scope.instance) {
                        $scope.reset();
                    }
                });
            }
        ]
    };
});
