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
// 1. PAGE PLUGIN - registers "Cell Explainer" under Tools menu
// ============================================================================
arc.run(['$rootScope', function ($rootScope) {
    $rootScope.plugin("cellExplainer", "Cell Explainer", "page", {
        menu: "tools",
        icon: "fa-search-plus",
        description: "AI-powered cell explanation: variance, transactions, root cause",
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
    $rootScope.plugin("cellExplainerFromCube", "Explain a Cell with AI", "menu/cube", {
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
            // Stash the cube name on $rootScope so the page directive can pick it up.
            // (Arc doesn't expose a direct "open this page" API in public docs;
            //  if $rootScope.openPage exists in your Arc build, use it instead.)
            $rootScope.cellExplainerPending = {
                instance: instance,
                cube: name,
                triggeredAt: Date.now()
            };

            // Try to navigate / open the page if Arc exposes such an API.
            // Otherwise broadcast for any already-open instance to pick up.
            $rootScope.$broadcast("cellExplainer.openWithCube", {
                instance: instance,
                cube: name
            });

            Notification.info({
                title: "<i class='fa fa-magic'></i> Cell Explainer",
                message: "Opening with cube <strong>" + name + "</strong>. " +
                         "If the page doesn't open automatically, click Tools → Cell Explainer."
            });
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

                // ============ STATE ============
                $scope.state = {
                    step: "paste",
                    cube: "",
                    tuple: [],
                    value: null,
                    action: null,
                    baseline: "Plan",
                    result: null,
                    error: null
                };
                $scope.baselines = ["Plan", "Forecast", "Prior Period", "Prior Year"];

                // ============ PICK UP PRE-FILL FROM menu/cube ACTION ============
                // If user came here via right-clicking a cube, the menu service
                // stashed the cube name on $rootScope. Use it.
                if ($rootScope.cellExplainerPending &&
                    $rootScope.cellExplainerPending.instance === $scope.instance) {
                    $scope.state.cube = $rootScope.cellExplainerPending.cube;
                    delete $rootScope.cellExplainerPending;
                }

                // Also listen for broadcasts while the page is already open
                $scope.$on("cellExplainer.openWithCube", function (event, args) {
                    if (args.instance === $scope.instance) {
                        $scope.state.cube = args.cube;
                        // If we're past the paste step, do nothing else
                        // (don't clobber user's in-progress analysis)
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
                    if (!$scope.state.cube) {
                        $scope.state.error = "Please enter the cube name.";
                        return;
                    }
                    $scope.state.action = action;
                    $scope.state.step = "analyzing";
                    $scope.state.error = null;

                    var payload = {
                        instance: $scope.instance,
                        cube: $scope.state.cube,
                        tuple: $scope.state.tuple,
                        value: $scope.state.value,
                        action: action,
                        baseline: $scope.state.baseline
                    };

                    $http.post(BACKEND_URL + "/api/explain-cell", payload, { timeout: 60000 })
                        .then(function (response) {
                            $scope.state.result = response.data;
                            $scope.state.step = "result";
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

                $scope.reset = function () {
                    $scope.state.step = "paste";
                    $scope.state.tuple = [];
                    $scope.state.value = null;
                    $scope.state.action = null;
                    $scope.state.result = null;
                    $scope.state.error = null;
                    // Keep cube name — user is likely staying in same cube
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
