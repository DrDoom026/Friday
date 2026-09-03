"""PART 13: communication adapters.

Provider-independent message shapes (:mod:`app.comm.models`) and the
adapter interface (:mod:`app.comm.adapter`) that every external
communication platform (Gmail today; Telegram/SMS/etc. later) implements.
Concrete adapters live in their own subpackage, e.g. :mod:`app.comm.gmail`.
"""
