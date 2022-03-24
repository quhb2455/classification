import numpy as np
import pandas as pd
from tqdm import tqdm
import json

from glob import glob
import os

from sklearn.model_selection import train_test_split
import albumentations as A

import cv2
from easydict import EasyDict


def label_preprocessing(path):
    #     path = os.path.join(path, 'train.csv')
    labels = pd.read_csv(path)

    cnt = 0
    label_encoder = {}
    for i, label in enumerate(tqdm(sorted(labels['label']))):

        if label not in label_encoder.values():
            label_encoder[cnt] = label
            cnt += 1

    label_decoder = {val: key for key, val in label_encoder.items()}

    return label_encoder, label_decoder


def csv_feature_dict(path, csv_features):
    csv_files = sorted(glob(os.path.join(path, '*/*.csv')))

    temp_csv = pd.read_csv(csv_files[0])[csv_features]
    max_arr, min_arr = temp_csv.max().to_numpy(), temp_csv.min().to_numpy()

    # feature 별 최대값, 최솟값 계산
    for csv in tqdm(csv_files[1:]):
        temp_csv = pd.read_csv(csv)[csv_features]
        temp_csv = temp_csv.replace('-', np.nan).dropna()
        if len(temp_csv) == 0:
            continue
        temp_csv = temp_csv.astype(float)
        temp_max, temp_min = temp_csv.max().to_numpy(), temp_csv.min().to_numpy()
        max_arr = np.max([max_arr, temp_max], axis=0)
        min_arr = np.min([min_arr, temp_min], axis=0)

    # feature 별 최대값, 최솟값 dictionary return
    return {csv_features[i]: [min_arr[i], max_arr[i]] for i in range(len(csv_features))}


def data_split(path, label_decoder, kfold=False, test_size=0.2):
    imgs = glob(os.path.join(path, '*/*.jpg'))
    json_files = glob(os.path.join(path, '*/*.json'))

    label_list = []
    for json_path in tqdm(json_files):
        json_file = json.load(open(json_path, 'r'))

        crop = json_file["annotations"]["crop"]
        disease = json_file["annotations"]["disease"]
        risk = json_file["annotations"]["risk"]

        label = f'{crop}_{disease}_{risk}'
        label_list.append(label_decoder[label])

    if kfold:
        return imgs, label_list
    else:
        return train_test_split(imgs, test_size=test_size, shuffle=True, stratify=label_list)

def transform(size=224):
    train_transforms = A.Compose([
        A.Resize(size, size),
        A.OneOf([
            A.Rotate(),
            A.HorizontalFlip(),
            A.VerticalFlip()
        ], p=1)
    ])

    val_transforms = A.Compose([
        A.Resize(size, size)
    ])

    return train_transforms, val_transforms

def rand_bbox(size, lam):
    W = size[2]
    H = size[3]
    cut_rat = np.sqrt(1. - lam)
    cut_w = int(W * cut_rat)
    cut_h = int(H * cut_rat)

    # uniform
    cx = np.random.randint(W)
    cy = np.random.randint(H)

    bbx1 = np.clip(cx - cut_w // 2, 0, W)
    bby1 = np.clip(cy - cut_h // 2, 0, H)
    bbx2 = np.clip(cx + cut_w // 2, 0, W)
    bby2 = np.clip(cy + cut_h // 2, 0, H)

    return bbx1, bby1, bbx2, bby2

