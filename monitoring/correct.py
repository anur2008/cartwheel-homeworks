"""Bias-corrected failure prevalence for a monitoring period."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np


def corrected_mode_prevalence(
    sample_preds: Sequence[int],
    test_labels: Sequence[int],
    test_preds: Sequence[int],
    confidence: float = 0.95,
    bootstrap_iterations: int = 20000,
    seed: int | None = 7,
) -> dict[str, Any]:
    """Bias-corrected live prevalence for one mode from sampled verdicts.

    The contract, precisely:

      1. ``raw`` is the uncorrected flag rate: ``mean(sample_preds)``.
      2. Compute the frozen judge's failure sensitivity and pass specificity
         from ``test_labels`` and ``test_preds``. Both use the monitoring
         convention that 1 means a failure is present. Failure sensitivity is
         the flagged fraction of human-labeled failures. Pass specificity is
         the unflagged fraction of human-labeled passes.
      3. Compute the Rogan-Gladen point estimate, then resample the held-out
         records and sampled predictions to obtain a percentile-bootstrap
         interval. Use a seeded NumPy generator so the committed result is
         reproducible.
      4. Resample the monitoring predictions and the paired held-out records
         independently with replacement. Keep their original sample sizes.
         Discard a draw if the correction cannot be computed. Clamp each
         retained estimate to [0, 1], then take the percentile interval.
         Raise ``ValueError`` if no replicate is valid.

    Args:
        sample_preds: the judge's 0/1 verdicts over the UNIFORM BASE sample
            only (never the risk strata; they are biased toward failure by
            design).
        test_labels: human labels for the frozen Homework 5 judge's test
            split.
        test_preds: the frozen judge's predictions on that test split.
        confidence: interval confidence level.
        bootstrap_iterations: number of percentile-bootstrap replicates.
        seed: numpy seed for a reproducible interval; None leaves the RNG
            untouched.

    Returns:
        {"raw", "corrected", "ci_low", "ci_high", "confidence",
         "failure_sensitivity", "pass_specificity", "n_sample"}
        with "corrected" clamped to [0, 1] and rates rounded to 4 places.

    Raises:
        ValueError: if an input is empty, the held-out inputs have different
            lengths, a value is not 0 or 1, a class is absent, the judge is
            missing a usable correction, or no bootstrap replicate is valid.
    """
    if not sample_preds or not test_labels or not test_preds:
        raise ValueError("sample predictions and held-out test data must be nonempty")
    if len(test_labels) != len(test_preds):
        raise ValueError("test_labels and test_preds must have the same length")
    if any(v not in (0, 1) for v in [*sample_preds, *test_labels, *test_preds]):
        raise ValueError("every label and prediction must be 0 or 1")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")

    sample = np.asarray(sample_preds, dtype=float)
    labels = np.asarray(test_labels, dtype=int)
    preds = np.asarray(test_preds, dtype=int)
    if labels.sum() == 0 or labels.sum() == len(labels):
        raise ValueError("the held-out labels need both failures and passes")

    raw = float(sample.mean())
    sensitivity = float(preds[labels == 1].mean())
    specificity = float(1 - preds[labels == 0].mean())
    youden = sensitivity + specificity - 1
    if youden <= 0:
        raise ValueError("the judge is no better than chance, so it cannot be corrected")
    corrected = min(1.0, max(0.0, (raw + specificity - 1) / youden))

    rng = np.random.default_rng(seed)
    n, m = len(sample), len(labels)
    raw_draws = sample[rng.integers(0, n, size=(bootstrap_iterations, n))].mean(axis=1)
    test_idx = rng.integers(0, m, size=(bootstrap_iterations, m))
    label_draws, pred_draws = labels[test_idx], preds[test_idx]
    failures = label_draws.sum(axis=1)
    passes = m - failures
    flagged_failures = (pred_draws * label_draws).sum(axis=1)
    flagged_passes = (pred_draws * (1 - label_draws)).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sens_draws = flagged_failures / failures
        spec_draws = 1 - flagged_passes / passes
        youden_draws = sens_draws + spec_draws - 1
        estimates = (raw_draws + spec_draws - 1) / youden_draws
    valid = (failures > 0) & (passes > 0) & (youden_draws > 0)
    if not valid.any():
        raise ValueError("no bootstrap replicate produced a usable correction")
    estimates = np.clip(estimates[valid], 0.0, 1.0)
    alpha = (1 - confidence) / 2
    ci_low, ci_high = np.percentile(estimates, [100 * alpha, 100 * (1 - alpha)])

    return {
        "raw": round(raw, 4),
        "corrected": round(corrected, 4),
        "ci_low": round(float(ci_low), 4),
        "ci_high": round(float(ci_high), 4),
        "confidence": confidence,
        "failure_sensitivity": round(sensitivity, 4),
        "pass_specificity": round(specificity, 4),
        "n_sample": n,
    }
