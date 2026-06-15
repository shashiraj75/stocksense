"""
Learning Alpha Engine
=====================
A self-improving prediction system that learns which factors actually predict
forward returns in the Indian large-cap universe.

Modules:
  store          — SQLite persistence for predictions + outcomes
  ic_engine      — Information Coefficient computation and IC-weighted factor weights
  regime_cluster — Unsupervised KMeans regime detection on market features
  meta_model     — Ridge / XGBoost meta-model trained on logged outcomes
  optimizer      — Mean-variance portfolio weight optimizer (scipy)
  outcome_logger — Resolves pending predictions against actual price returns
  weight_adapter — Daily adaptation loop: log outcomes → recompute IC → retrain model
"""
