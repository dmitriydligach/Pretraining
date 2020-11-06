#!/usr/bin/env python3

from torch.utils.data import TensorDataset, RandomSampler, SequentialSampler, DataLoader

import sys, os, pickle
sys.path.append('../Lib/')
sys.path.append('../Codes/')

from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score

import configparser, torch, shutil

# my python modules
from dataphenot import DatasetProvider
import bow, utils, metrics

# local model path
model_path = 'Model/model.pt'
model_dir = 'Model/'

def make_data_loader(model_inputs, model_outputs, batch_size, partition):
  """DataLoader objects for train or dev/test sets"""

  tensor_dataset = TensorDataset(model_inputs, model_outputs)

  # use sequential sampler for dev and test
  if partition == 'train':
    sampler = RandomSampler(tensor_dataset)
  else:
    sampler = SequentialSampler(tensor_dataset)

  data_loader = DataLoader(
    tensor_dataset,
    sampler=sampler,
    batch_size=batch_size)

  return data_loader

def fit(model, train_loader, val_loader, weights, n_epochs):
  """Training routine"""

  device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
  model.to(device)

  weights = weights.to(device)
  cross_entropy_loss = torch.nn.CrossEntropyLoss(weights)

  optimizer = torch.optim.Adam(
    model.parameters(),
    lr=cfg.getfloat('model', 'lr'))

  best_roc_auc = 0
  optimal_epochs = 0

  for epoch in range(1, n_epochs + 1):
    model.train()
    train_loss, num_train_steps = 0, 0

    for batch in train_loader:
      optimizer.zero_grad()

      batch = tuple(t.to(device) for t in batch)
      batch_inputs, batch_outputs = batch

      logits = model(batch_inputs)
      loss = cross_entropy_loss(logits, batch_outputs)
      loss.backward()

      torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
      optimizer.step()

      train_loss += loss.item()
      num_train_steps += 1

    av_tr_loss = train_loss / num_train_steps
    val_loss, val_roc_auc = evaluate(model, val_loader, weights)
    print('ep: %d, steps: %d, tr loss: %.4f, val loss: %.4f, val roc: %.4f' % \
          (epoch, num_train_steps, av_tr_loss, val_loss, val_roc_auc))

    if val_roc_auc > best_roc_auc:
      print('roc auc improved...')
      best_roc_auc = val_roc_auc
      optimal_epochs = epoch

  return best_roc_auc, optimal_epochs

def evaluate(model, data_loader, weights):
  """Evaluation routine"""

  device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
  weights = weights.to(device)
  model.to(device)

  cross_entropy_loss = torch.nn.CrossEntropyLoss(weights)
  total_loss, num_steps = 0, 0

  model.eval()

  all_labels = []
  all_probs = []

  for batch in data_loader:
    batch = tuple(t.to(device) for t in batch)
    batch_inputs, batch_outputs = batch

    with torch.no_grad():
      logits = model(batch_inputs)
      loss = cross_entropy_loss(logits, batch_outputs)

    batch_logits = logits.detach().to('cpu')
    batch_outputs = batch_outputs.to('cpu')
    batch_probs = torch.nn.functional.softmax(batch_logits, dim=1)[:, 1]

    all_labels.extend(batch_outputs.tolist())
    all_probs.extend(batch_probs.tolist())

    total_loss += loss.item()
    num_steps += 1

  av_loss = total_loss / num_steps
  roc_auc = roc_auc_score(all_labels, all_probs)

  return av_loss, roc_auc

def eval_on_dev():
  """Split train into train and dev and fit"""

  # load model configuration
  pkl = open(cfg.get('data', 'config_pickle'), 'rb')
  config = pickle.load(pkl)

  # instantiate model and load parameters
  model = bow.BagOfWords(**config, save_config=False)
  state_dict = torch.load(cfg.get('data', 'model_file'))
  model.load_state_dict(state_dict)
  model.eval()

  # new classification layer
  model.classifier = torch.nn.Linear(
    in_features=config['hidden_units'],
    out_features=2)

  # load training data first
  train_data_provider = DatasetProvider(
    os.path.join(base, cfg.get('data', 'train')),
    cfg.get('data', 'tokenizer_pickle'))

  x_train, y_train = train_data_provider.load_as_int_seqs()
  x_train, x_val, y_train, y_val = train_test_split(
    x_train, y_train, test_size=0.10, random_state=2020)

  x_train = utils.sequences_to_matrix(
    x_train,
    config['input_vocab_size'])
  x_val = utils.sequences_to_matrix(
    x_val,
    config['input_vocab_size'])

  train_loader = make_data_loader(
    x_train,
    torch.tensor(y_train),
    cfg.getint('model', 'batch'),
    'train')
  val_loader = make_data_loader(
    x_val,
    torch.tensor(y_val),
    cfg.getint('model', 'batch'),
    'dev')

  label_counts = torch.bincount(torch.tensor(y_train))
  weights = len(y_train) / (2.0 * label_counts)

  best_roc_auc, optimal_epochs = fit(
    model,
    train_loader,
    val_loader,
    weights,
    cfg.getint('model', 'epochs'))
  print('best roc %.4f after %d epochs' % (best_roc_auc, optimal_epochs))

  return optimal_epochs

def eval_on_test(n_epochs):
  """Train on training set and evaluate on test"""

  # load model configuration
  pkl = open(cfg.get('data', 'config_pickle'), 'rb')
  config = pickle.load(pkl)

  # instantiate model and load parameters
  model = bow.BagOfWords(**config, save_config=False)
  state_dict = torch.load(cfg.get('data', 'model_file'))
  model.load_state_dict(state_dict)
  model.eval()

  # new classification layer
  model.classifier = torch.nn.Linear(
    in_features=config['hidden_units'],
    out_features=2)

  # load training data first
  train_data_provider = DatasetProvider(
    os.path.join(base, cfg.get('data', 'train')),
    cfg.get('data', 'tokenizer_pickle'))

  # now load the test set
  test_data_provider = DatasetProvider(
    os.path.join(base, cfg.get('data', 'test')),
    cfg.get('data', 'tokenizer_pickle'))

  x_train, y_train = train_data_provider.load_as_int_seqs()
  x_test, y_test = test_data_provider.load_as_int_seqs()

  x_train = utils.sequences_to_matrix(
    x_train,
    config['input_vocab_size'])
  x_val = utils.sequences_to_matrix(
    x_test,
    config['input_vocab_size'])

  train_loader = make_data_loader(
    x_train,
    torch.tensor(y_train),
    cfg.getint('model', 'batch'),
    'train')
  val_loader = make_data_loader(
    x_val,
    torch.tensor(y_test),
    cfg.getint('model', 'batch'),
    'dev')

  label_counts = torch.bincount(torch.tensor(y_train))
  weights = len(y_train) / (2.0 * label_counts)

  fit(model, train_loader, val_loader, weights, n_epochs)

if __name__ == "__main__":

  cfg = configparser.ConfigParser()
  cfg.read(sys.argv[1])
  base = os.environ['DATA_ROOT']

  if os.path.isdir(model_dir):
    shutil.rmtree(model_dir)
  os.mkdir(model_dir)

  optimal_epochs = eval_on_dev()
  eval_on_test(optimal_epochs)
