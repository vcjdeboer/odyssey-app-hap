"""Vendored slice of the plr_v4 prototype — Odyssey support only.

This package is bundled into odyssey-app-hap as a transitional
convenience: the upstream-canonical home for this code is
``pylabrobot.li_cor.odyssey`` and ``pylabrobot.capabilities.scanning``
on PR https://github.com/PyLabRobot/pylabrobot/pulls?q=author:vcjdeboer
(branch ``vcjdeboer/pylabrobot:odyssey-v1b1``). When that PR merges,
this directory will be removed and the app's imports will switch to
``pylabrobot.li_cor.odyssey``.

Only the modules the app actually needs are vendored — the broader
plr_v4 prototype (Arduino, Titrino, SevenDirect titration devices)
is not included here.
"""
