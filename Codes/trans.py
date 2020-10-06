#!/usr/bin/env python3

import sys
sys.path.append('../Lib/')

import torch
import torch.nn as nn

from torch.utils.data import TensorDataset, DataLoader
from torch.utils.data import RandomSampler, SequentialSampler

from sklearn.model_selection import train_test_split

from transformer import EncoderLayer

import os, configparser, random, pickle
import data, utils

# deterministic determinism
torch.manual_seed(2020)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False
random.seed(2020)

# model and model config locations
model_path = 'Model/model.pt'
config_path = 'Model/config.p'

class TransformerEncoder(nn.Module):

  def __init__(
    self,
    input_vocab_size,
    output_vocab_size,
    d_model,
    d_inner,
    n_head,
    d_k,
    d_v,
    hidden_units,
    dropout_rate,
    save_config=True):
    """Constructor"""

    super(TransformerEncoder, self).__init__()

    self.embed = nn.Embedding(
      num_embeddings=input_vocab_size,
      embedding_dim=d_model)

    self.trans = EncoderLayer(
      d_model=d_model,
      d_inner=d_inner,
      n_head=n_head,
      d_k=d_k,
      d_v=d_v)

    self.hidden = nn.Linear(
      in_features=d_model,
      out_features=hidden_units)

    self.activation = nn.ReLU()

    self.dropout = nn.Dropout(dropout_rate)

    self.classifier = nn.Linear(
      in_features=hidden_units,
      out_features=output_vocab_size)

    # save configuration for loading later
    if save_config:
      config = {
        'input_vocab_size': input_vocab_size,
        'output_vocab_size': output_vocab_size,
        'd_model': d_model,
        'd_inner': d_inner,
        'n_head': n_head,
        'd_k': d_k,
        'd_v': d_v,
        'hidden_units': hidden_units,
        'dropout_rate': dropout_rate}
      pickle_file = open(config_path, 'wb')
      pickle.dump(config, pickle_file)

  def forward(self, texts, return_hidden=False):
    """Optionally return hidden layer activations"""

    output = self.embed(texts)
    output, _ = self.trans(output)
    output = torch.mean(output, dim=1)
    features = self.hidden(output)     # pretrained representation
    output = self.activation(features) # maybe return these instead
    output = self.dropout(output)
    output = self.classifier(output)

    if return_hidden:
      return features
    else:
      return output

def make_data_loader(input_seqs, model_outputs, batch_size, partition):
  """DataLoader objects for train or dev/test sets"""

  model_inputs = utils.pad_sequences(input_seqs, max_len=None)

  # e.g. transformers take input ids and attn masks
  if type(model_inputs) is tuple:
    tensor_dataset = TensorDataset(*model_inputs, model_outputs)
  else:
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

def fit(model, train_loader, val_loader, n_epochs):
  """Training routine"""

  device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
  model.to(device)

  criterion = nn.BCEWithLogitsLoss()

  optimizer = torch.optim.Adam(
    model.parameters(),
    lr=cfg.getfloat('model', 'lr'))

  best_loss = float('inf')
  optimal_epochs = 0

  for epoch in range(1, n_epochs + 1):

    model.train()
    train_loss, num_train_steps = 0, 0

    for batch in train_loader:

      optimizer.zero_grad()

      batch = tuple(t.to(device) for t in batch)
      batch_inputs, batch_outputs = batch

      logits = model(batch_inputs)
      loss = criterion(logits, batch_outputs)
      loss.backward()

      torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
      optimizer.step()

      train_loss += loss.item()
      num_train_steps += 1

    av_tr_loss = train_loss / num_train_steps
    val_loss = evaluate(model, val_loader)
    print('ep: %d, steps: %d, tr loss: %.4f, val loss: %.4f' % \
          (epoch, num_train_steps, av_tr_loss, val_loss))

    if val_loss < best_loss:
      print('loss improved, saving model...')
      torch.save(model.state_dict(), model_path)
      best_loss = val_loss
      optimal_epochs = epoch

  return best_loss, optimal_epochs

def evaluate(model, data_loader):
  """Evaluation routine"""

  device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
  model.to(device)

  criterion = nn.BCEWithLogitsLoss()
  total_loss, num_steps = 0, 0

  model.eval()

  for batch in data_loader:

    batch = tuple(t.to(device) for t in batch)
    batch_inputs, batch_outputs = batch

    with torch.no_grad():
      logits = model(batch_inputs)
      loss = criterion(logits, batch_outputs)

    total_loss += loss.item()
    num_steps += 1

  av_loss = total_loss / num_steps
  return av_loss
 
def main():
  """My main main"""

  dp = data.DatasetProvider(
    os.path.join(base, cfg.get('data', 'cuis')),
    os.path.join(base, cfg.get('data', 'codes')),
    cfg.get('args', 'cui_vocab_size'),
    cfg.get('args', 'code_vocab_size'))
  in_seqs, out_seqs = dp.load_as_sequences()

  tr_in_seqs, val_in_seqs, tr_out_seqs, val_out_seqs = train_test_split(
    in_seqs, out_seqs, test_size=0.10, random_state=2020)

  print('loaded %d training and %d validation samples' % \
        (len(tr_in_seqs), len(val_in_seqs)))

  max_cui_seq_len = max(len(seq) for seq in tr_in_seqs)
  max_code_seq_len = max(len(seq) for seq in tr_out_seqs)
  print('longest cui sequence:', max_cui_seq_len)
  print('longest code sequence:', max_code_seq_len)

  train_loader = make_data_loader(
    tr_in_seqs,
    utils.sequences_to_matrix(tr_out_seqs, len(dp.output_tokenizer.stoi)),
    cfg.getint('model', 'batch'),
    'train')

  val_loader = make_data_loader(
    val_in_seqs,
    utils.sequences_to_matrix(val_out_seqs, len(dp.output_tokenizer.stoi)),
    cfg.getint('model', 'batch'),
    'dev')

  model = TransformerEncoder(
    input_vocab_size=len(dp.input_tokenizer.stoi),
    output_vocab_size=len(dp.output_tokenizer.stoi),
    d_model = cfg.getint('model', 'd_model'),
    d_inner = cfg.getint('model', 'd_inner'),
    n_head = cfg.getint('model', 'n_head'),
    d_k = cfg.getint('model', 'd_k'),
    d_v = cfg.getint('model', 'd_v'),
    hidden_units=cfg.getint('model', 'hidden'),
    dropout_rate=cfg.getfloat('model', 'dropout'))

  best_loss, optimal_epochs = fit(
    model,
    train_loader,
    val_loader,
    cfg.getint('model', 'epochs'))
  print('best loss %.4f after %d epochs' % (best_loss, optimal_epochs))

if __name__ == "__main__":

  cfg = configparser.ConfigParser()
  cfg.read(sys.argv[1])
  base = os.environ['DATA_ROOT']

  main()