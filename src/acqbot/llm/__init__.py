"""Language-model layer — Section 7 and 11.

Two models, two jobs (7.1): a small fast one extracts facts from every inbound message, a
stronger one writes the reply. Neither computes a price, decides the stage, or sees the
valuation before PRICED. Every call is stored whole in `model_calls`.
"""
