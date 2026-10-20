"""Missing prices stay missing in display and manual records."""
from math import isfinite
from numbers import Real


def valid_price(value):
    return isinstance(value, Real) and not isinstance(value, bool) and isfinite(value) and value > 0


def position_error(side, entry, target, stop):
    if not all(valid_price(value) for value in (entry,target,stop)):
        return 'Giriş, hedef ve stop sonlu ve sıfırdan büyük olmalı.'
    if side == 'LONG' and stop < entry < target:
        return None
    if side == 'SHORT' and target < entry < stop:
        return None
    return 'LONG için stop < giriş < hedef; SHORT için hedef < giriş < stop olmalı.'
