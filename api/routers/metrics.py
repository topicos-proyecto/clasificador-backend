"""Métricas comparativas de los tres modelos, estadísticas del dataset y evaluación final en test.

Solo se devuelven agregados: nunca textos de mensajes de evaluación.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_admin
from api.config import settings
from api.model import model_or_503
from api.routers.classifier import SUMMARY_KEYS, read_json, verified_report

router = APIRouter(prefix='/metrics', dependencies=[Depends(require_admin)])
EVAL_KEYS = ('records', 'accuracy', 'balanced_accuracy', 'f1_macro', 'f1_weighted', 'classification_report',
             'confusion_matrix_labels', 'confusion_matrix')


def clean_eval(value: dict | None) -> dict | None:
    return {k: value.get(k) for k in EVAL_KEYS} if isinstance(value, dict) else None


@router.get('/models')
def models():
    model = model_or_503()
    report, current = verified_report(model)
    if 'finalists' not in report:
        raise HTTPException(status_code=503, detail='El informe no incluye la comparación de tres modelos (v3).')
    try:
        finalists = {}
        for name, f in report['finalists'].items():
            finalists[name] = {
                'config': f['config'], 'is_selected': f['is_selected'],
                'best_by_mean': {k: f['best_by_mean'].get(k) for k in SUMMARY_KEYS},
                'cv': {'f1_macro_mean': f['cv']['f1_macro_mean'], 'f1_macro_std': f['cv']['f1_macro_std'],
                       'fold_f1_macro': f['cv']['fold_f1_macro'], 'out_of_fold': clean_eval(f['cv'].get('out_of_fold'))},
                'evaluations': {k: clean_eval(f.get(k)) for k in ('validation', 'validation_real', 'simulated') if k in f},
                'cost': f['cost'], 'vocabulary_size': f['vocabulary_size']}
        grouped = report.get('grouped_sensitivity') or {}
        return {'model_sha256': current, 'selection_rule': report['selection'],
                'selected': {k: report['selected'].get(k) for k in SUMMARY_KEYS},
                'best_by_mean': report['best_by_mean'], 'threshold': report['threshold'],
                'finalists': finalists, 'variants': report.get('variants', []),
                'paired': [{k: p[k] for k in ('a', 'b', 'fold_differences_a_minus_b', 'mean_difference', 'std_difference')}
                           for p in report.get('pairwise_comparisons', [])],
                'grouped_sensitivity': {'n_groups': grouped.get('n_groups'),
                                        'finalists': [{k: g.get(k) for k in (*SUMMARY_KEYS, 'grouped_f1_macro_mean')}
                                                      for g in grouped.get('finalists', [])]} if grouped else None,
                'n_configurations': report['n_configurations'], 'grid': report['grid'],
                'counts': report['counts'], 'seed': report['seed'], 'folds': report['folds'],
                'labels': report['validation']['confusion_matrix_labels'], 'test_used': report['test_used']}
    except (KeyError, TypeError) as exc:
        raise HTTPException(status_code=503, detail='El informe comparativo está incompleto.') from exc


@router.get('/dataset')
def dataset():
    model = model_or_503()
    _, current = verified_report(model)
    stats = read_json(settings.dataset_path)
    recorded = stats.get('preprocessing', {}).get('model_sha256')
    if recorded is not None and recorded != current:
        raise HTTPException(status_code=503, detail='Las estadísticas del dataset no corresponden al modelo cargado.')
    return stats


@router.get('/test')
def test_report():
    model = model_or_503()
    _, current = verified_report(model)
    if not settings.test_path.is_file():
        return {'estado': 'pendiente'}
    report = read_json(settings.test_path)
    if report.get('model_sha256') != current:
        return {'estado': 'pendiente', 'detalle': 'La evaluación en test corresponde a otro modelo.'}
    return {'estado': 'evaluado', 'records': report['records'], 'selected_model': report['selected_model'],
            'note': report.get('note'),
            'finalists': {name: {'config': f['config'], 'is_selected': f['is_selected'], **clean_eval(f)}
                          for name, f in report['finalists'].items()}}
