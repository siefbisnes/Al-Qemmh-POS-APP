(function () {
  const openButton = document.getElementById("openCompleteForm");
  const form = document.getElementById("completeForm");
  const paymentInput = document.getElementById("paymentAmountInput");
  const remainingOutput = document.getElementById("remainingAfterPayment");

  if (!openButton || !form || !paymentInput || !remainingOutput) return;

  const state = window.customerPurchaseState || { remaining: 0 };

  openButton.addEventListener("click", () => {
    form.hidden = false;
    paymentInput.focus();
  });

  paymentInput.addEventListener("input", () => {
    const paidNow = Number(paymentInput.value || 0);
    const remaining = Math.max(state.remaining - paidNow, 0);
    remainingOutput.value = remaining.toFixed(2) + " EGP";
  });
})();
