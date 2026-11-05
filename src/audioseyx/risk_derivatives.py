"""Derivatives of the fixed-choice nested native objective, including penalties."""
from __future__ import annotations
import numpy as np

from .receiver_calibration import smooth_risk, _risk_layout


def risk_value_gradient(values, masses, weight, temperature):
    values = np.asarray(values, float)
    masses = np.asarray(masses, float)
    value = float(smooth_risk(values, masses, weight, temperature))
    positive = masses > 0
    mass = masses[positive]/masses[positive].max()
    mass /= mass.sum()
    with np.errstate(over='ignore'):
        exponential = mass*np.exp((values[positive]-values[positive].max())/temperature)
    exponential /= exponential.sum()
    derivative = np.zeros(len(values))
    derivative[positive] = (1-weight)*mass+weight*exponential
    return value, derivative


def fixed_choice_row_gradient(table, losses, settings, crossovers):
    """Exact scalar objective and partial derivative for every retained row."""
    losses = np.asarray(losses, float)
    risk = float(settings.get('receiver_position_risk', .35))
    temperature = float(settings.get('receiver_risk_temperature', .15))
    curve_risk = float(settings.get('receiver_curve_risk', .35))
    curve_temperature = float(settings.get('receiver_curve_risk_temperature', .15))
    curve_weights = table['curve_weights']
    names = list(curve_weights)
    group_values, derivatives, group_masses = [], [], []
    penalty = float(settings.get('receiver_crossover_change_penalty', .002))
    for group, variants in zip(table['groups'], _risk_layout(table)):
        hz = crossovers[group['id']]
        profiles = variants[hz]
        values, local = [], []
        for name in names:
            indices, masses = profiles[name]
            value, grad = risk_value_gradient(losses[indices], masses, risk, temperature)
            values.append(value)
            local.append((indices, grad))
        value, curve_grad = risk_value_gradient(values, list(curve_weights.values()), curve_risk, curve_temperature)
        gradient = np.zeros(len(losses))
        for factor, (indices, grad) in zip(curve_grad, local):
            gradient[indices] = factor*grad
        group_values.append(value+(penalty*np.log2(hz/group['current_hz'])**2 if hz is not None else 0))
        derivatives.append(gradient)
        group_masses.append(group.get('mass', len(group['channels'])))
    value, group_grad = risk_value_gradient(group_values, group_masses, risk, temperature)
    return value, group_grad@np.array(derivatives)
