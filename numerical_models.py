"""TB-06B offline FreqAI fit hooks on immutable Maj Vaj folds.

Train only on the declared train rows, calibrate on validation, then freeze
native text boosters. Evaluation is a distinct immutable receipt. No strategy,
exchange, network, orders, pickle loading or model promotion.
"""
import hashlib
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from data_lifecycle import _private_dir,_publish_directory,_sync_tree
from market_memory import MemoryUnavailable,sha256_file
from numerical_dataset import verify as verify_dataset,model_rows,QUALIFICATION
from training_handoff import canonical,HEX,PAIR
from engine import IMAGE

FORMAT=1
MODEL_LIMIT=16*1024**2
MANIFEST_LIMIT=1024*1024
MIN_CLASS=5
EPS=1e-6
PARAMS={'n_estimators':64,'learning_rate':0.05,'num_leaves':15,'min_child_samples':10,
        'n_jobs':2,'verbosity':-1,'random_state':42,'deterministic':True,'force_col_wise':True}
COHORTS=('numerical_only','no_sessions')
MODEL_FILES=('regressor.txt','classifier.txt')
RUNTIME='2026.10'

def require(condition,message):
    if not condition:raise MemoryUnavailable(message)

def runtime():
    import freqtrade,lightgbm,numpy,pandas,sklearn
    require(str(freqtrade.__version__).split('-')[0]==RUNTIME,'unexpected Freqtrade runtime')
    return {'freqtrade':str(freqtrade.__version__),'lightgbm':lightgbm.__version__,
            'numpy':numpy.__version__,'pandas':pandas.__version__,'sklearn':sklearn.__version__,
            'expected_image':IMAGE,'fit_contract':'upstream LightGBMRegressor/Classifier.fit; fixed external folds'}

def matrix(rows):
    import numpy as np
    x=np.array(rows['X'],dtype=np.float64)
    require(x.ndim==2 and x.shape==(len(rows['y']),len(rows['columns'])) and not np.isinf(x).any(),'invalid model matrix')
    require(not np.isnan(x).all(axis=0).any(),'unobserved training feature column')
    return x

def paired(directory,sha,pair,fold,cohort):
    import numpy as np
    reg=model_rows(directory,sha,pair,fold,cohort,'&-net_return')
    cls=model_rows(directory,sha,pair,fold,cohort,'&-net_positive')
    require(reg['columns']==cls['columns'] and reg['decisions']==cls['decisions'] and reg['X']==cls['X'],'model target cohorts disagree')
    x=matrix(reg); y=np.asarray(reg['y'],dtype=float); z=np.asarray(cls['y'],dtype=int)
    require(np.isfinite(y).all() and np.array_equal(z,(y>0).astype(int)),'target mismatch')
    return x,y,z,reg['decisions'],reg['columns']

def class_support(y):
    return [int((y==0).sum()),int((y==1).sum())]

def fit_native(x,y,pair,kind,scratch):
    """Invoke the actual upstream fit implementation, with real DataKitchen.

    We intentionally do not use FreqAI train() (which would split the data
    again) or start() (which would attach a trading runtime).
    """
    import numpy as np
    import pandas as pd
    from freqtrade.freqai.prediction_models.LightGBMRegressor import LightGBMRegressor
    from freqtrade.freqai.prediction_models.LightGBMClassifier import LightGBMClassifier
    from freqtrade.freqai.data_kitchen import FreqaiDataKitchen
    cfg={'user_data_dir':scratch,'timeframe':'1h','exchange':{'pair_whitelist':[pair]},
         'freqai':{'enabled':True,'identifier':kind,'activate_tensorboard':False,
                   'continual_learning':False,'data_kitchen_thread_count':2,
                   'data_split_parameters':{'test_size':0,'shuffle':False},
                   'feature_parameters':{'include_timeframes':['1h'],'include_corr_pairlist':[]},
                   'model_training_parameters':dict(PARAMS)}}
    klass=LightGBMRegressor if kind=='regressor' else LightGBMClassifier
    instance=klass(cfg)
    dk=FreqaiDataKitchen(cfg,live=True,pair=pair)
    dk.set_paths(pair,0);dk.data_path.mkdir(parents=True,exist_ok=True)
    # No eval_set, early stopping, warm-start or test data reaches fit().
    data={'train_features':pd.DataFrame(x,columns=[f'f{i}' for i in range(x.shape[1])]),
          'train_labels':pd.DataFrame(y,columns=['target']),
          'train_weights':np.ones(len(y))}
    model=instance.fit(data,dk)
    if kind=='classifier':require(list(model.classes_)==[0,1],'unexpected classifier class order')
    require(model.booster_.num_trees()>0,'empty fitted booster')
    return model.booster_

def logit(p):
    import numpy as np
    p=np.clip(np.asarray(p,dtype=float),EPS,1-EPS)
    return np.log(p/(1-p))

def calibrate(probability,y):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    require(min(class_support(y))>=MIN_CLASS,'insufficient validation class support for calibration')
    p=np.asarray(probability,dtype=float)
    require(p.shape==y.shape and np.isfinite(p).all() and ((p>=0)&(p<=1)).all(),'invalid raw probability')
    clf=LogisticRegression(C=1.0,solver='lbfgs',max_iter=1000,random_state=42)
    clf.fit(logit(p).reshape(-1,1),y)
    require(int(clf.n_iter_[0])<1000,'calibration did not converge')
    a,b=float(clf.coef_[0,0]),float(clf.intercept_[0])
    require(math.isfinite(a) and math.isfinite(b),'invalid calibration coefficients')
    return {'method':'validation_only_regularized_logistic_logit','slope':a,'intercept':b,'epsilon':EPS}

def calibrated(p,cal):
    import numpy as np
    require(cal['method']=='validation_only_regularized_logistic_logit' and cal['epsilon']==EPS and all(math.isfinite(cal[k]) for k in ('slope','intercept')),'invalid calibration contract')
    z=np.clip(cal['slope']*logit(p)+cal['intercept'],-60,60)
    return 1/(1+np.exp(-z))

def scores(y,z,reg,raw,prob):
    import numpy as np
    require(all(a.shape==y.shape and np.isfinite(a).all() for a in (reg,raw,prob)),'invalid evaluation prediction')
    require(((raw>=0)&(raw<=1)&(prob>=0)&(prob<=1)).all(),'invalid probability range')
    def loss(p):
        p=np.clip(p,EPS,1-EPS)
        return {'brier':float(np.mean((p-z)**2)),
                'log_loss':float(-np.mean(z*np.log(p)+(1-z)*np.log1p(-p)))}
    bins=[]
    for i in range(10):
        mask=(prob>=i/10)&((prob<(i+1)/10) if i<9 else (prob<=1))
        n=int(mask.sum())
        if n:bins.append({'bin':i,'count':n,'mean_probability':float(prob[mask].mean()),'observed_rate':float(z[mask].mean())})
    return {'rows':len(y),'classes':class_support(z),'mae':float(np.mean(np.abs(y-reg))),
            'rmse':float(np.sqrt(np.mean((y-reg)**2))),'raw':loss(raw),
            'calibrated':loss(prob),'reliability':bins}

def source_contract():
    root=Path(__file__).resolve().parent
    return {name:sha256_file(root/name) for name in ('numerical_models.py','numerical_features.py','numerical_dataset.py')}

def prepare_destination(destination,source):
    p=Path(destination).absolute();_private_dir(p.parent);p=p.parent.resolve()/p.name
    require(not p.exists() and not p.is_symlink() and p!=Path(source).resolve() and Path(source).resolve() not in p.parents,'new private destination required')
    return p

def write_json(path,val):
    with path.open('x') as h:h.write(canonical(val));h.flush();os.fsync(h.fileno())

def artifact(path):
    size=path.stat().st_size
    require(0<size<=MODEL_LIMIT,'model artifact size outside bounds')
    return {'bytes':size,'sha256':sha256_file(path)}

def fit(directory,sha,destination,pair,cohort):
    """No test-fold model matrix is loaded by fitting or calibration."""
    require(cohort in COHORTS and PAIR.fullmatch(pair),'unsupported numerical model cohort')
    data=verify_dataset(directory,sha)
    require(pair in [m['pair'] for m in data['markets']],'pair outside dataset')
    versions=runtime()
    destination=prepare_destination(destination,directory)
    stage=Path(tempfile.mkdtemp(prefix='.model-fit-',dir=destination.parent))
    try:
        x,y,z,dates,columns=paired(directory,sha,pair,'train',cohort)
        v,vy,vz,vdates,vcolumns=paired(directory,sha,pair,'validation',cohort)
        require(columns==vcolumns and max(dates)<min(vdates) and min(class_support(z))>=MIN_CLASS and min(class_support(vz))>=MIN_CLASS,'inadequate fit or calibration classes/chronology')
        with tempfile.TemporaryDirectory(prefix='.freqai-fit-',dir=destination.parent) as tmp:
            reg=fit_native(x,y,pair,'regressor',Path(tmp))
            cls=fit_native(x,z,pair,'classifier',Path(tmp))
        raw=cls.predict(v,num_threads=2)
        calibration=calibrate(raw,vz)
        validation=scores(vy,vz,reg.predict(v,num_threads=2),raw,calibrated(raw,calibration))
        reg.save_model(str(stage/MODEL_FILES[0]));cls.save_model(str(stage/MODEL_FILES[1]))
        files={name:artifact(stage/name) for name in MODEL_FILES}
        manifest={'format':FORMAT,'kind':'maj-vaj-frozen-numerical-model','dataset_sha256':sha,
                  'snapshot_sha256':data['snapshot_sha256'],'snapshot_cutoff':data['snapshot_cutoff'],
                  'pair':pair,'cohort':cohort,'columns':list(columns),'params':dict(PARAMS),
                  'runtime':versions,'source_contract':source_contract(),'train_rows':len(y),'train_classes':class_support(z),'validation':validation,
                  'calibration':calibration,'artifacts':files,'final_test_evaluated':False,**QUALIFICATION}
        write_json(stage/'manifest.json',manifest);_sync_tree(stage);_publish_directory(stage,destination)
        return manifest|{'manifest_sha256':sha256_file(destination/'manifest.json')}
    finally:
        if stage.exists():shutil.rmtree(stage)

def verify_model(directory,expected):
    directory=Path(directory).absolute();_private_dir(directory);p=directory/'manifest.json'
    require(isinstance(expected,str) and HEX.fullmatch(expected) and not p.is_symlink() and p.is_file() and 0<p.stat().st_size<=MANIFEST_LIMIT and sha256_file(p)==expected,'model manifest checksum mismatch')
    m=json.loads(p.read_text())
    require(m.get('format')==FORMAT and m.get('kind')=='maj-vaj-frozen-numerical-model' and m.get('cohort') in COHORTS and m.get('params')==PARAMS and m.get('final_test_evaluated') is False and all(m.get(k) is v for k,v in QUALIFICATION.items()),'invalid model manifest')
    require(set(m['artifacts'])==set(MODEL_FILES) and {x.name for x in directory.iterdir()}=={'manifest.json',*MODEL_FILES},'invalid model inventory')
    require(m['runtime']==runtime() and m['source_contract']==source_contract(),'model runtime or feature/fit implementation mismatch')
    for name in MODEL_FILES:
        file=directory/name
        require(not file.is_symlink() and file.is_file() and artifact(file)==m['artifacts'][name],'model artifact checksum mismatch')
    return m|{'manifest_sha256':expected}

def _load_text(directory,m,name):
    """Only locally trusted, digest-pinned native LightGBM text; no pickle."""
    from lightgbm import Booster
    p=Path(directory)/name;entry=m['artifacts'][name]
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as f:raw=f.read(entry['bytes']+1)
    require(len(raw)==entry['bytes'] and hashlib.sha256(raw).hexdigest()==entry['sha256'] and raw.startswith(b'tree\n'),'invalid native model text')
    model=Booster(model_str=raw.decode('utf-8'))
    require(model.num_feature()==len(m['columns']) and model.num_trees()>0,'model feature count mismatch')
    return model

def evaluate(directory,sha,model_dir,model_sha,destination):
    """No fitting, no calibration adjustment, no overwriting evaluation."""
    data=verify_dataset(directory,sha);m=verify_model(model_dir,model_sha)
    require(m['dataset_sha256']==sha and m['snapshot_sha256']==data['snapshot_sha256'] and m['snapshot_cutoff']==data['snapshot_cutoff'] and m['columns']==data['cohorts'][m['cohort']],'model and evaluation dataset differ')
    destination=prepare_destination(destination,model_dir)
    require(destination!=Path(directory).resolve() and Path(directory).resolve() not in destination.parents,'evaluation cannot mutate dataset')
    stage=Path(tempfile.mkdtemp(prefix='.model-evaluate-',dir=destination.parent))
    try:
        x,y,z,dates,columns=paired(directory,sha,m['pair'],'test',m['cohort'])
        require(list(columns)==m['columns'],'evaluation feature schema changed')
        reg=_load_text(model_dir,m,MODEL_FILES[0]);cls=_load_text(model_dir,m,MODEL_FILES[1])
        prediction=reg.predict(x,num_threads=2);raw=cls.predict(x,num_threads=2);prob=calibrated(raw,m['calibration'])
        metric=scores(y,z,prediction,raw,prob)
        with (stage/'predictions.jsonl').open('x') as h:
            for i,d in enumerate(dates):
                h.write(canonical({'decision':d,'predicted_net_return':float(prediction[i]),
                                   'raw_positive_probability':float(raw[i]),'calibrated_positive_probability':float(prob[i]),
                                   'actual_scenario_return':float(y[i]),'actual_positive':int(z[i])})+'\n')
            h.flush();os.fsync(h.fileno())
        receipt={'format':FORMAT,'kind':'maj-vaj-frozen-test-evaluation','model_sha256':model_sha,
                 'dataset_sha256':sha,'pair':m['pair'],'cohort':m['cohort'],'metrics':metric,
                 'predictions':artifact(stage/'predictions.jsonl'),'first_decision':min(dates),'last_decision':max(dates),
                 'evaluation_only':True,**QUALIFICATION}
        write_json(stage/'manifest.json',receipt);_sync_tree(stage);_publish_directory(stage,destination)
        return receipt|{'manifest_sha256':sha256_file(destination/'manifest.json')}
    finally:
        if stage.exists():shutil.rmtree(stage)

def verify_evaluation(directory,expected,model_dir,model_sha,dataset,sha):
    directory=Path(directory).absolute();_private_dir(directory)
    p=directory/'manifest.json'
    require(isinstance(expected,str) and HEX.fullmatch(expected) and not p.is_symlink() and p.is_file() and 0<p.stat().st_size<=MANIFEST_LIMIT and sha256_file(p)==expected,'evaluation manifest checksum mismatch')
    m=json.loads(p.read_text())
    require(m.get('format')==FORMAT and m.get('kind')=='maj-vaj-frozen-test-evaluation' and m.get('evaluation_only') is True and m.get('model_sha256')==model_sha and m.get('dataset_sha256')==sha and all(m.get(k) is v for k,v in QUALIFICATION.items()),'invalid evaluation custody')
    pred=directory/'predictions.jsonl'
    require({x.name for x in directory.iterdir()}=={'manifest.json','predictions.jsonl'} and not pred.is_symlink() and pred.is_file() and artifact(pred)==m['predictions'],'invalid evaluation prediction inventory')
    # Re-evaluate the *same* frozen model and data; never fit or recalibrate.
    with tempfile.TemporaryDirectory(prefix='.eval-verify-',dir=directory.parent) as tmp:
        produced=evaluate(dataset,sha,model_dir,model_sha,Path(tmp)/'evaluation')
        require(produced==m|{'manifest_sha256':expected},'frozen evaluation does not reproduce')
    return m|{'manifest_sha256':expected}
