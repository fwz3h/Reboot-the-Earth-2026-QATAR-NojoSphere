"""
model.py -- placeholder for the machine-learning part of Mazraa.

RIGHT NOW THIS IS NOT REAL MACHINE LEARNING.
It is a small hand-written rule that returns a sensible-looking number, so the
whole app works end-to-end today. A teammate will replace it with a real PyTorch
model that was trained on real yield records.

Keep the function signature `predict_yield_ml(features)` exactly as it is: the
rest of the app (engine.py) already calls it, so when the real model is ready you
only change the inside of this file.

How to swap in the real model later:
  1. Train a regression model that predicts tonnes per hectare from features like
     [average temperature, yearly rainfall, solar radiation, soil pH, area, crop
      one-hot, system one-hot].
  2. Save it next to this file, e.g. backend/yield_model.pt plus a small
     backend/feature_scaler.json.
  3. In predict_yield_ml(): load the model once (see _load_model() below), build
     the feature vector, call model(...), and return tonnes per hectare.
"""

import os

# Path where the future trained PyTorch model will live.
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yield_model.pt")

# Loaded model is kept here so we only pay the loading cost once.
_MODEL = None


def _load_model():
    """
    Load the trained PyTorch model from disk, once.

    This is intentionally not implemented yet -- torch is not installed and there
    is no trained model file. When the teammate finishes the model, the code will
    look roughly like this:

        import torch
        _MODEL = torch.jit.load(MODEL_PATH)
        _MODEL.eval()

    Until then it simply returns None and predict_yield_ml() uses its rough rule.
    """
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    # TODO(teammate): implement the real load with torch.jit.load(MODEL_PATH)
    return None


def predict_yield_ml(features):
    """
    Return a YIELD CORRECTION FACTOR (roughly 0.70 to 1.20).

    engine.calculate_economics() multiplies its formula-based yield by this
    factor. A value above 1.0 means "the model thinks this spot will do a bit
    better than the plain formula", below 1.0 means worse.

    Parameters
    ----------
    features : dict
        {
          "crop": "Tomato",            # crop display name
          "system": "greenhouse",       # key from engine.SYSTEMS
          "area": 2.5,                  # hectares
          "suitability": 0.81,          # 0-1, from engine.score_suitability()
          "avg_temp_c": 24.0,           # may be None if unknown
          "rainfall_mm": 600.0,         # may be None if unknown
          "soil_ph": 6.8,               # may be None if unknown
        }

    Returns
    -------
    float
        The correction factor.
    """
    model = _load_model()
    if model is not None:
        # TODO(teammate): build the feature vector and run the real model here,
        # for example:
        #     import torch
        #     with torch.no_grad():
        #         predicted = model(torch.tensor(_to_vector(features))).item()
        #     return predicted
        pass

    # ------------------------------------------------------------------
    # PLACEHOLDER RULE (delete once the real model exists)
    # ------------------------------------------------------------------
    suitability = features.get("suitability")
    if suitability is None:
        suitability = 0.7

    # A better-suited site keeps a little extra of its yield.
    factor = 0.90 + (0.20 * float(suitability))

    # Extreme heat or cold hurts more in the field than the smooth formula says.
    temperature = features.get("avg_temp_c")
    if temperature is not None and (temperature > 35.0 or temperature < 2.0):
        factor -= 0.10

    # Very large farms are usually run a little less intensively per hectare.
    area = features.get("area") or 0.0
    if area > 50.0:
        factor -= 0.03

    # Keep the factor inside a sane band so a bug cannot explode the output.
    return round(max(0.70, min(1.20, factor)), 3)
