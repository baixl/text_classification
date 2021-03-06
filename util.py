# coding: UTF-8
import os
import torch
import numpy as np
import pickle as pkl
from tqdm import tqdm  # 进度条工具
import time
from datetime import timedelta

MAX_VOCAB_SIZE = 10000  # 词表长度限制
UNK, PAD, CLS = '<UNK>', '<PAD>', '[CLS]'  # 未知字，padding符号


def build_vocab(file_path, tokenizer, max_size, min_freq):
    """
        1、词典按频次排序， 取频次大于 min_freq的部分构造词典， 并添加 unk 和pad
    """
    vocab_dict = {}
    with open(file_path, 'r', encoding='UTF-8') as f:
        for line in tqdm(f):
            line = line.strip()
            if not line:
                continue
            content = line.split('\t')[0]
            for word in tokenizer(content):
                vocab_dict[word] = vocab_dict.get(word, 0) + 1

        vocab_list = sorted(
            [item for item in vocab_dict.items() if item[1] >= min_freq],
            key=lambda x: x[1],
            reverse=True)[:max_size]

        vocab_dict = {
            word_count[0]: idx
            for idx, word_count in enumerate(vocab_list)
        }

        vocab_dict.update({UNK: len(vocab_dict), PAD: len(vocab_dict) + 1})
    return vocab_dict


def build_dataset(config, ues_word=False):
    if ues_word:
        def tokenizer(x): return x.split(' ')  # 以空格隔开，word-level
    else:
        def tokenizer(x): return [y for y in x]  # char-level, 以字为单位
    if os.path.exists(config.vocab_path):
        vocab = pkl.load(open(config.vocab_path, 'rb'))
    else:
        vocab = build_vocab(config.train_path,
                            tokenizer=tokenizer,
                            max_size=MAX_VOCAB_SIZE,
                            min_freq=1)
        pkl.dump(vocab, open(config.vocab_path, 'wb'))

    print("Vocab size: {0} ".format(len(vocab)))

    def biGramHash(sequence, t, buckets):
        t1 = sequence[t - 1] if t - 1 >= 0 else 0
        return (t1 * 14918087) % buckets

    def triGramHash(sequence, t, buckets):
        t1 = sequence[t - 1] if t - 1 >= 0 else 0
        t2 = sequence[t - 2] if t - 2 >= 0 else 0
        return (t2 * 14918087 * 18408749 + t1 * 14918087) % buckets

    def load_dataset(path, pad_size=32):
        contents = []
        with open(path, 'r', encoding='UTF-8') as f:
            for line in tqdm(f):
                line = line.strip()
                if not line:
                    continue
                content, label = line.split('\t')

                token = tokenizer(content)
                seq_len = len(token)
                if pad_size:
                    if len(token) < pad_size:
                        token.extend([vocab.get(PAD)] *
                                     (pad_size - len(token)))
                    else:
                        token = token[:pad_size]
                        seq_len = pad_size
                # word to id
                words_line = []
                for word in token:
                    words_line.append(vocab.get(word, vocab.get(UNK)))

                if config.model_name == "FastText":
                    # fastText ngram
                    buckets = config.n_gram_vocab
                    bigram = []
                    trigram = []
                    # ------ngram------
                    for i in range(pad_size):
                        bigram.append(biGramHash(words_line, i, buckets))
                        trigram.append(triGramHash(words_line, i, buckets))
                    # -----------------
                    contents.append(
                        (words_line, int(label), seq_len, bigram, trigram))
                else:
                    contents.append((words_line, int(label), seq_len))  # 3列

        return contents  # [([...], 0), ([...], 1), ...]

    train = load_dataset(config.train_path, config.pad_size)
    dev = load_dataset(config.dev_path, config.pad_size)
    test = load_dataset(config.test_path, config.pad_size)
    return vocab, train, dev, test


def build_dataset_bert(config):
    def load_dataset(path, pad_size=32):
        contents = []
        with open(path, 'r', encoding='UTF-8') as f:
            for line in tqdm(f):
                line = line.strip()
                if not line:
                    continue
                content, label = line.split('\t')
                token = config.tokenizer.tokenize(content)

                token = [CLS] + token
                seq_len = len(token)
                mask = []
                token_ids = config.tokenizer.convert_tokens_to_ids(token)

                if pad_size:
                    if len(token) < pad_size:
                        mask = [1] * len(token_ids) + [0] * \
                            (pad_size - len(token))
                        token_ids += ([0] * (pad_size - len(token)))
                    else:
                        mask = [1] * pad_size
                        token_ids = token_ids[:pad_size]
                contents.append((token_ids, int(label), seq_len, mask))
        return  contents

    train = load_dataset(config.train_path, config.pad_size)
    dev = load_dataset(config.dev_path, config.pad_size)
    test = load_dataset(config.test_path, config.pad_size)
    return train, dev, test


class DatasetIterater(object):
    def __init__(self, batches, batch_size, device, model_name):
        self.batch_size = batch_size
        self.batches = batches
        self.n_batches = len(batches) // batch_size
        self.residue = False  # 记录batch数量是否为整数
        if len(batches) % self.n_batches != 0:
            self.residue = True
        self.index = 0
        self.device = device
        self.model_name = model_name

    def _to_tensor(self, datas):
        x = torch.LongTensor([_[0] for _ in datas]).to(self.device)
        y = torch.LongTensor([_[1] for _ in datas]).to(self.device)

        # pad前的长度(超过pad_size的设为pad_size)
        seq_len = torch.LongTensor([_[2] for _ in datas]).to(self.device)

        # fast text
        if self.model_name == "FastText":  # fastText 需要 bigram  trigram 特征
            bigram = torch.LongTensor([_[3] for _ in datas]).to(self.device)
            trigram = torch.LongTensor([_[4] for _ in datas]).to(self.device)
            return (x, seq_len, bigram, trigram), y
        # bert
        elif "Bert" in self.model_name  or "bert" in self.model_name:
            mask = torch.LongTensor([_[3] for _ in datas]).to(self.device)
            return (x, seq_len, mask), y
        else:
            return (x, seq_len), y

        

    def __next__(self):
        if self.residue and self.index == self.n_batches:
            batches = self.batches[self.index *
                                   self.batch_size:len(self.batches)]
            self.index += 1
            batches = self._to_tensor(batches)
            return batches

        elif self.index > self.n_batches:
            self.index = 0
            raise StopIteration
        else:
            batches = self.batches[self.index *
                                   self.batch_size:(self.index + 1) *
                                   self.batch_size]
            self.index += 1
            batches = self._to_tensor(batches)
            return batches

    def __iter__(self):
        return self

    def __len__(self):
        if self.residue:
            return self.n_batches + 1
        else:
            return self.n_batches


def build_iterator(dataset, config):
    iter = DatasetIterater(dataset, config.batch_size,
                           config.device, config.model_name)
    return iter


def get_time_dif(start_time):
    """获取已使用时间"""
    end_time = time.time()
    time_dif = end_time - start_time
    return timedelta(seconds=int(round(time_dif)))
