// The shop page's sign-in (panel/signin.py): a link that came with #parts (a QR code at the bins) opens Parts after
// signing in. Plain JavaScript, no build step.
(function () {
  "use strict";
  var a = document.getElementById("signin");
  var tab = (location.hash || "").slice(1);
  if (a && /^(shafts|parts|plates)$/.test(tab)) a.setAttribute("href", "/shop/sign-in?tab=" + tab);
})();
