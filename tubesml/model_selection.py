__author__ = 'lucabasa'
__version__ = '1.1.1'
__status__ = 'development'

import pandas as pd
import numpy as np

from sklearn.model_selection import GridSearchCV, RandomizedSearchCV
from sklearn.model_selection import StratifiedShuffleSplit, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.base import clone

from tubesml.report import get_coef, get_feature_importance
from tubesml.base import BaseTransformer


def grid_search(data, target, estimator, param_grid, scoring, cv, random=False):
    '''
    Calls a grid or a randomized search over a parameter grid
    
    :param data: pandas DataFrame.
           Data to tune the hyperparameters
           
    :param target: numpy array or pandas Series.
            Target column
            
    :param estimator: sklearn compatible estimator.
            It must have a ``predict`` method and a ``get_params`` method.
            It can be a Pipeline.
            
    :param param_grid: dict.
            Dictionary of the parameter space to explore.
            In case the ``estimator`` is a pipeline, provide the keys in the format ``step__param``.
            
    :param scoring: string.
            Scoring metric for the grid search, see the sklearn documentation for the available options.
            
    :param cv: KFold object or int.
            For cross-validation.
            
    :param random: bool, default=False.
            If True, runs a RandomSearch instead of a GridSearch.
    
    :return: a dataframe with the results for each configuration
    :return: a dictionary with the best parameters
    :return: the best (fitted) estimator
    '''
    
    if random:
        grid = RandomizedSearchCV(estimator=estimator, param_distributions=param_grid, cv=cv, scoring=scoring, 
                                  n_iter=random, n_jobs=-1, random_state=434, return_train_score=True, error_score='raise')
    else:
        grid = GridSearchCV(estimator=estimator, param_grid=param_grid, error_score='raise',
                            cv=cv, scoring=scoring, n_jobs=-1, return_train_score=True)
    
    pd.options.mode.chained_assignment = None  # turn on and off a warning of pandas
    tmp = data.copy()
    grid = grid.fit(tmp, target)
    pd.options.mode.chained_assignment = 'warn'
    
    result = pd.DataFrame(grid.cv_results_).sort_values(by='mean_test_score', 
                                                        ascending=False).reset_index()
    
    del result['params']
    times = [col for col in result.columns if col.endswith('_time')]
    params = [col for col in result.columns if col.startswith('param_')]
    
    result = result[params + ['mean_train_score', 'std_train_score', 'mean_test_score', 'std_test_score'] + times]
    
    return result, grid.best_params_, grid.best_estimator_


def cv_score(data, target, estimator, cv, imp_coef=False, predict_proba=False, early_stopping=None, eval_metric=None, verbose=False):
    '''
    Train and test a pipeline in kfold cross validation
    
    :param data: pandas DataFrame.
           Data to tune the hyperparameters.
           
    :param target: numpy array or pandas Series.
            Target column.
            
    :param estimator: sklearn compatible estimator.
            It must have a ``predict`` method and a ``get_params`` method.
            It can be a Pipeline. If it is not a Pipeline, it will be made one for compatibility with other functionalities.
            
    :param cv: KFold object.
            For cross-validation, the estimates will be done across these folds.
            
    :param imp_coef: bool, default=False.
            If True, returns the feature importance or the coefficient values averaged across the folds, with standard deviation on the mean.
            
    :param predict_proba: bool, default=False.
            If True, calls the ``predict_proba`` method instead of the ``predict`` one.
            
    :param early_stopping: bool, default=False.
                        If True, uses early stopping within the folds for the estimators that support it.
                        
    :param eval_metric: str, default=None.
                        The evaluation metric to use for early stopping.
                        
    :param verbose: bool or int, default=False.
                        Level of verbosity for early stopping.
    
    :return oof: pd.Series with the out of fold predictions for the entire train set.
    
    :return rep_res: A dictionary with additional results. If ``imp_coef=True``, it contains a pd.DataFrame with the coefficients or 
                    feature importances of the estimator, it can be found under the key ``feat_imp``. If ``early_stopping=True``, it contains a list 
                    with the best iteration number per fold, it can be found under the key ``iterations``.
    '''
    oof = np.zeros(len(data))
    train = data.copy()
    
    rep_res = {}
    
    feat_df = pd.DataFrame()
    iteration = []
    
    try:  # If estimator is not a pipeline, make a pipeline
        estimator.steps
    except AttributeError:
        estimator = Pipeline([('transf', BaseTransformer()), ('model', estimator)])
    
    for n_fold, (train_index, test_index) in enumerate(cv.split(train.values)):
            
        trn_data = train.iloc[train_index, :]
        val_data = train.iloc[test_index, :]
        
        trn_target = pd.Series(target.iloc[train_index].values.ravel())
        val_target = pd.Series(target.iloc[test_index].values.ravel())
        
        if early_stopping:
            # create model and transform pipelines
            transf_pipe = clone(Pipeline(estimator.steps[:-1]))
            model = clone(estimator.steps[-1][1])
            # Transform the data for the model
            trn_data = transf_pipe.fit_transform(trn_data, trn_target)
            val_data = transf_pipe.transform(val_data)
            # Fit the model with early stopping
            model.fit(trn_data, trn_target, 
                      eval_set=[(trn_data, trn_target), (val_data, val_target)], 
                      early_stopping_rounds=early_stopping,
                      eval_metric=eval_metric,
                      verbose=verbose)
            #store iteration used
            try:
                iteration.append(model.best_iteration)
            except AttributeError:
                iteration.append(model.best_iteration_)
        else:
            model = clone(estimator)  # it creates issues with match_cols in dummy otherwise
            model.fit(trn_data, trn_target)
        
        if predict_proba:
            oof[test_index] = model.predict_proba(val_data)[:,1]
        else:
            oof[test_index] = model.predict(val_data).ravel()

        if imp_coef:
            if early_stopping:
                feats = trn_data.columns
            else:
                feats = None
            try:
                fold_df = get_coef(model, feats)
            except (AttributeError, KeyError):
                fold_df = get_feature_importance(model, feats)
                
            fold_df['fold'] = n_fold + 1
            feat_df = pd.concat([feat_df, fold_df], axis=0)
    
    if imp_coef:
        feat_df = feat_df.groupby('feat')['score'].agg(['mean', 'std'])
        feat_df['abs_sco'] = (abs(feat_df['mean']))
        feat_df = feat_df.sort_values(by=['abs_sco'],ascending=False)
        feat_df['std'] = feat_df['std'] / np.sqrt(cv.get_n_splits() - 1)  # std of the mean, unbiased
        del feat_df['abs_sco']
        rep_res['feat_imp'] = feat_df
    if early_stopping:
        rep_res['iterations'] = iteration
        
    return oof, rep_res
    
    
def make_test(train, test_size, random_state, strat_feat=None):
    '''
    Creates a train and test, stratified on a feature or on a list of features.
    
    :param train: pandas DataFrame.
    
    :param test_size: float.
                        The size of the test set. It must be between 0 and 1.
                        
    :param random_state: int.
                        Random state used to split the data.
                        
    :param strat_feat: str or list, default=None.
                        The feature or features to use to stratify the split.
            
    :return: A train set and a test set.
    '''
    if strat_feat:
        
        split = StratifiedShuffleSplit(n_splits=1, test_size=test_size, random_state=random_state)

        for train_index, test_index in split.split(train, train[strat_feat]):
            train_set = train.iloc[train_index, :]
            test_set = train.iloc[test_index, :]
            
    else:
        train_set, test_set = train_test_split(train, test_size=test_size, random_state=random_state)
            
    return train_set, test_set
