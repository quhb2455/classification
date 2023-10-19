from trainer import Trainer
from predictor import Predictor
from datasets import DatasetCreater
from models import BaseModel, DivBaseModel, MultiHeadBaseModel, ColorClassifierHead, BinaryClassifierHead
from loss_fn import FocalLoss, TripletMargingLoss
from optim_fn import SAM
from utils import save_config, mixup, cutmix, score, get_loss_weight, set_seed, cal_cls_report, Multi_cutmix

import torch
import torch.nn as nn
from torch.optim import SGD, Adam, AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR
import albumentations as A
from albumentations.pytorch import ToTensorV2
from albumentations.core.transforms_interface import ImageOnlyTransform
# from pytorch_metric_learning import miners, losses

import cv2
import numpy as np
from tqdm import tqdm
import pandas as pd

class CreateMeanTileImage(ImageOnlyTransform):
    def __init__(
        self,
        min_size=10,
        max_size=15,
        num=10, 
        always_apply=False,
        p=1
    ):
        super(CreateMeanTileImage, self).__init__(always_apply, p)
        self.min_size=min_size
        self.max_size=max_size
        self.num=num
        
    def apply(self, img, **params):
        return self.create_mean_image(img)
    
    def create_mean_image(self, img) :
        rgb = [int(img[:, :, i].mean()) for i in range(3)]
        img_h, img_w = img.shape[:2]
        for _ in range(self.num) :
            tile_w = np.random.randint(self.min_size, self.max_size)
            tile_h = np.random.randint(self.min_size, self.max_size)
            y1 = np.random.randint(1, img_h)
            x1 = np.random.randint(1, img_w)
            y2 = y1 + tile_h
            x2 = x1 + tile_w
            img[y1 : y2, x1: x2, :] = rgb
        return np.uint8(img)
    
    def get_transform_init_args_names(self):
        return ("min_size", "max_size", "num")
    
class BaseMain(Trainer, Predictor, DatasetCreater) :
    def __init__(self, **cfg) -> None:
        super().__init__()
        # self.model = BaseModel(**cfg).to(cfg["device"])
        self.model = DivBaseModel(**cfg).to(cfg["device"])
        
        # self.model = MultiHeadBaseModel(**cfg).to(cfg["device"])
        # in_dim = list(self.model.backbone.children())[-1].in_features
        # self.binary_head = BinaryClassifierHead(in_dim, 128).to(cfg["device"])
        # self.low_head = ColorClassifierHead(in_dim, 128, 10).to(cfg["device"])
        # self.upper_head = ColorClassifierHead(in_dim, 128, 8).to(cfg["device"])
        
        self.optimizer = Adam(self.model.parameters(), lr=cfg["learning_rate"])
        # self.optimizer = Adam([
        #     {"params":self.model.parameters()},
        #     # {"params" : self.binary_head.parameters()},
        #     {"params" : self.low_head.parameters(), 'lr' : 1e-4},
        #     {"params" : self.upper_head.parameters()},], lr=cfg["learning_rate"])
        
        # self.optimizer = SAM(self.model.parameters(), SGD, weight_decay=0.0001, momentum=0.9,lr=cfg["learning_rate"])
        # self.criterion = FocalLoss(alpha=cfg["focal_alpha"],gamma=cfg["focal_gamma"]).to(cfg["device"])
        self.ce_criterion = nn.CrossEntropyLoss().to(cfg["device"])
        # self.bce_criterion = nn.BCELoss().to(cfg["device"])
        self.metric_criterion = TripletMargingLoss().to(cfg["device"])
        # self.scheduler = CosineAnnealingLR(self.optimizer.base_optimizer, T_max=cfg['epochs'], eta_min=0.008)
        
        self.valid_output_chk = []
        self.valid_gt_chk = []
        
        if cfg["mode"] == 'train' :
            self.train_loader, self.valid_loader = self.create_dataloader([self.get_transform('train', **cfg), 
                                                                           self.get_transform('valid', **cfg)], **cfg)
            
        elif cfg["mode"] == 'infer' :
            self.test_loader = self.create_dataloader(self.get_transform('infer', **cfg), **cfg)
            
    def train(self, **cfg) :
        self.run(**cfg)
        
    def train_on_epoch(self, epoch, **cfg):
        self.model.train()
        # self.binary_head.train()
        # self.low_head.train()
        # self.upper_head.train()
        train_acc, train_loss = [], []
        # train_upper_loss, train_lower_loss = [], []
        tqdm_train = tqdm(self.train_loader)
        for step, (img, label) in enumerate(tqdm_train) :
            batch_res = self.train_on_batch(img, label, step, **cfg)
            
            train_acc.append(batch_res["acc"])
            train_loss.append(batch_res["loss"])
            # train_upper_loss.append(batch_res["upper_loss"])
            # train_lower_loss.append(batch_res["lower_loss"])
            
            log = {
                "Epoch" : epoch,
                "Training Acc" : np.mean(train_acc),
                "Training Loss" : np.mean(train_loss),
                # "Training Upper Loss" : np.mean(train_upper_loss),
                # "Training Lower Loss" : np.mean(train_lower_loss),
            }
            tqdm_train.set_postfix(log)
        
        self.logging(log, epoch)    
        self.scheduler_step()
        
    def train_on_batch(self, img, label, step, **cfg) :
        self.optimizer.zero_grad()

        img = img.to(cfg["device"])
        label = label.to(cfg["device"])
        # up_label = up_low[0].to(cfg["device"])
        # low_label = up_low[1].to(cfg["device"])
        
        # up_label = label.masked_select(up_low)
        # low_label = label.masked_select(~up_low)
        
        img, lam, label_a, label_b = cutmix(img, label) 
        # img, lam, label_a, label_b, up_label_a, up_label_b, low_label_a, low_label_b = Multi_cutmix(img, label, up_label, low_label)
        # output = torch.randn(cfg['batch_size'], cfg['num_classes'])
        emb, output = self.model(img, div=True)
        # emb = self.model(img)
        # masking = self.binary_head(emb)
        # upper_out = self.upper_head(emb)#, masking, mode='upper')
        # lower_out = self.low_head(emb)#, masking, mode='lower')
        
        ce_loss = lam * (self.ce_criterion(output, label_a)) + (1-lam) * (self.ce_criterion(output, label_b))
        # masking_loss = self.bce_criterion(masking, up_low.float().unsqueeze(1))
        # upper_loss = lam * torch.nan_to_num(self.ce_criterion(upper_out, up_label_a)) + (1-lam) * torch.nan_to_num(self.ce_criterion(upper_out, up_label_b))#self.ce_criterion(upper_out, up_label)
        # lower_loss = lam * torch.nan_to_num(self.ce_criterion(lower_out, low_label_a)) + (1-lam) * torch.nan_to_num(self.ce_criterion(lower_out, low_label_b))#self.ce_criterion(upper_out, up_label)
        m_loss = lam * self.metric_criterion(emb, label_a) + (1-lam) * self.metric_criterion(emb, label_b)
        loss = ce_loss + m_loss
        
        # loss = lam * self.criterion(output, label_a) + (1 - lam) * self.criterion(output, label_b) + lam * self.metric_criterion(emb, label_a) + (1 - lam) * self.metric_criterion(emb, label_b)
        
        # loss = self.criterion(output, label.type(torch.float32))
        loss.backward()
        self.optimizer.step()
        
        # acc = score(mixup_label, output)
        acc = score(label, output)
        # acc = score(label, torch.cat([lower_out, upper_out], dim=1))
        # acc = score(torch.argmax(label, dim=1), output)

        
        batch_metric = {
            "acc" : acc,
            "loss" : loss.item(),
            # "upper_loss" : upper_loss.item(),
            # "lower_loss" : lower_loss.item(),
        }
        
        return batch_metric 

    def valid_on_epoch(self, epoch, **cfg):
        self.model.eval()
        # self.low_head.eval()
        # self.upper_head.eval()
        valid_acc, valid_loss, valid_output = [], [], [[], []]
        # valid_upper_loss, valid_lower_loss = [], []
        tqdm_valid = tqdm(self.valid_loader)
        for step, (img, label) in enumerate(tqdm_valid) :
            batch_res = self.valid_on_batch(img, label, step, **cfg)
            
            valid_acc.append(batch_res["acc"])
            valid_loss.append(batch_res["loss"])
            valid_output[0].extend(batch_res['labelAcc'][0])
            valid_output[1].extend(batch_res['labelAcc'][1])
            
            # valid_upper_loss.append(batch_res["upper_loss"])
            # valid_lower_loss.append(batch_res["lower_loss"])
            log = {
                "Epoch" : epoch,
                "Validation Acc" : np.mean(valid_acc),
                "Validation Loss" : np.mean(valid_loss),
                # "Validation Upper Loss" : np.mean(valid_upper_loss),
                # "Validation Lower Loss" : np.mean(valid_lower_loss),
            }
            tqdm_valid.set_postfix(log)
            
        self.logging({"LabelAcc" : cal_cls_report(valid_output[0], valid_output[1])}, epoch, mode='multi')
        self.logging(log, epoch)   
        
        z = pd.DataFrame({"GT" : self.valid_gt_chk, "Output" : self.valid_output_chk})
        z.to_csv("./valid_chk.csv", index=False) 
        return np.mean(valid_acc), np.mean(valid_loss)
    
    def valid_on_batch(self, img, label,  step, **cfg):
        img = img.to(cfg["device"])
        label = label.to(cfg["device"])
        # up_label = up_low[0].to(cfg["device"])
        # low_label = up_low[1].to(cfg["device"])
        
        if cfg["binary_mode"] :
            mixup_label = torch.argmax(label, dim=1)
        
            output = self.model(img)
            loss = self.criterion(output, label.type(torch.float32))
            
            acc = score(mixup_label, output)
        else :        
            # output = self.model(img)
            output = self.model(img)
            # masking = self.binary_head(emb)
            # upper_out = self.upper_head(emb)#, masking, mode='upper')
            # lower_out = self.low_head(emb)#, masking, mode='lower')
            
            # upper_loss = self.ce_criterion(upper_out, up_label)
            # lower_loss = self.ce_criterion(lower_out, low_label)    
            loss = self.ce_criterion(output, label)
            # loss = upper_loss + lower_loss
            self.valid_output_chk.extend(output.argmax(1).detach().cpu().numpy().tolist()) 
            self.valid_gt_chk.extend(label.detach().cpu().numpy().tolist())
            
            acc, cls_report = score(label, output, mode="valid")
            # acc, cls_report = score(label, torch.cat([lower_out, upper_out], dim=1), mode="valid")
            
        batch_metric = {
            "acc" : acc,
            "loss" : loss.item(),
            # "upper_loss" : upper_loss.item(),
            # "lower_loss" : lower_loss.item(),
            "labelAcc" : cls_report
        }
        
        # self.logging({'LabelAcc' : cls_report}, step, mode='multi')
        return batch_metric
    
       
    def infer(self, **cfg) :
        return self.prediction(**cfg)
    
    
    def prediction(self, **cfg) :
        self.pred_weight_load(cfg["weight_path"],cfg["device"])
        self.model.eval()
        # self.low_head.eval()
        # self.upper_head.eval()     
        model_preds = []
        with torch.no_grad() :
            for img in tqdm(self.test_loader) :
                model_preds += self.predict_on_batch(img, **cfg)
        
        return self.save_to_csv(model_preds, **cfg)
        
    
    def predict_on_batch(self, img, **cfg) :
        img = img.to(cfg["device"])
        output = self.model(img)
        # upper_out = self.upper_head(emb)
        # lower_out = self.low_head(emb)
        return output.argmax(1).detach().cpu().numpy().tolist()
    
    
    def get_transform(self, _mode, **cfg) :
        resize = cfg["resize"]
        if _mode == 'train' :
            return A.Compose([
                A.Resize(resize, resize),
                # A.RandomBrightness((0.2, 0.3), p =1),
                A.OneOf([
                    A.CoarseDropout(min_holes=5, max_holes=10, max_height=8, min_height=8),
                    A.GlassBlur(sigma=1, max_delta=2),        
                    A.RandomRain(slant_lower=-10, slant_upper=10,  brightness_coefficient=1, drop_color=(10, 10, 10), p=1),
                    A.RingingOvershoot(blur_limit=(5, 5), p=1),
                    
                ], p=0.3),
                
                A.OneOf([
                    A.OpticalDistortion(p=1, border_mode=1,
                        distort_limit=3, shift_limit=1),
                    A.Affine(rotate=(-180, 180), fit_output=False, mask_interpolation=1, mode=3, p=0.3),
                ], p=0.3),
                CreateMeanTileImage(min_size=10, max_size=15, num=15,p=0.3),
                A.RandomGridShuffle(grid=(3, 3), p=0.3),
                A.ElasticTransform(alpha=10, sigma=10 * 0.25, alpha_affine=10 * 0.25, p=0.2), #>>
                A.Equalize(by_channels=False,p=1),
                A.Normalize(),
                # A.Normalize(mean=(0.7, 0.5, 0.1)),
                # A.Normalize(mean=(0.548172032,0.467046563,0.434142448),
                #             std=(0.12784231,0.122905336,0.119736256)),
                ToTensorV2()
            ])
        elif _mode == 'valid' :
            return A.Compose([
                A.Resize(resize, resize),
                # CreateMeanTileImage(min_size=10, max_size=15, num=30,p=1),
                # A.RandomGridShuffle(grid=(3, 3), p=1),
                # A.RandomBrightness((0.2, 0.3), p =1),
                # A.Normalize(mean=(0.7, 0.5, 0.1)),
                A.Equalize(by_channels=False,p=1),
                A.Normalize(),
                # A.Normalize(mean=(0.548172032,0.467046563,0.434142448),
                #             std=(0.12784231,0.122905336,0.119736256)),
                ToTensorV2()
            ])
        elif _mode == 'infer' : 
            return A.Compose([
                A.Resize(resize, resize),
                A.Normalize(),
                # A.Normalize(mean=(0.548172032,0.467046563,0.434142448),
                #             std=(0.12784231,0.122905336,0.119736256)),
                ToTensorV2()
            ])

    
if __name__ == "__main__" :
    
    cfg = {
        "mode" : "train", #train, #infer
        
        "model_name" : "seresnext50_32x4d", #"tf_efficientnetv2_m.in21k", #"swinv2_base_window12to16_192to256_22kft1k",
        # "tf_efficientnet_b4", #"resnetv2_101x1_bit", #"resnetv2_152x2_bit", #"resnetv2_50x1_bit",
        #"tf_efficientnetv2_s.in21k",#"eva_large_patch14_196.in22k_ft_in1k",, #"wide_resnet101_2", #"seresnext50_32x4d"
        #"beit_base_patch16_224.in22k_ft_in22k", #"convnextv2_base.fcmae_ft_in1k", #"seresnet101", #"seresnext101_64x4d"
        #"nf_resnet50",#"nfnet_f1"
        "num_classes" : 18,
        
        "learning_rate" : 5e-5,
        "focal_alpha" : 2,
        "focal_gamma" : 2,
        "resize" : 224,
        
        "data_train_path" : "./sub-task2/Dataset/Train",
        "data_train_csv_path" : "./sub-task2/Dataset/aug_info_etri20_color_train.csv",
        "data_valid_path" : "./sub-task2/Dataset/Validation",
        "data_valid_csv_path" : "./sub-task2/Dataset/info_etri20_color_validation.csv",
        
        "data_infer_path" : "/aif/Dataset/Test/",
        "data_infer_csv_path" : "/aif/Dataset/info_etri20_color_test.csv",
        # "data_infer_path" : "./sub-task2/Dataset/Test_sample",
        # "data_infer_csv_path" : "./sub-task2/Dataset/info_etri20_color_test_sample.csv",
        
        "epochs" : 80,
        "batch_size" : 32,
        "num_worker" : 4,
        "early_stop_patient" : 30,
        
        "reuse" : False, #True, #False
        "weight_path" : "./sub-task2/ckpt/seresnext50_32x4d/Equalize_CustomAug_SpatialAug/63E-val0.6227062319989951-seresnext50_32x4d.pth",
        
        "save_path" : "./sub-task2/ckpt/seresnext50_32x4d/Equalize_CustomAug_SpatialAug",
        "output_path" : "./sub-task2/output/seresnext50_32x4d/Equalize_CustomAug_SpatialAug",
        "log_path" : "./sub-task2/logging/Equalize_CustomAug_SpatialAug",
        "device" : "cuda",
        
        "binary_mode" : False,
        
        "loss_weight" : [0.8622, 1.2872, 1.0935, 0.9228, 0.9122, 1.0983, 1.1635, 1.0886,
                        1.015 , 1.1258, 0.9061, 0.9446, 0.9728, 0.8595, 0.9743, 0.9163,
                        0.9292, 0.9281],
        
        
        "seed": 2455,
        "note" : ["offline aug for distance smapling data",
                "Cutmix 사용", "CE Loss 사용", "Adam Optim 사용"]
    }        
    
    if cfg["mode"] == "train" :
        cfg["shuffle"] = True
    elif cfg["mode"] == "infer" :
        cfg["shuffle"] = False
    
    set_seed(cfg["seed"])
    save_config(cfg, cfg["save_path"], save_name=cfg["mode"]+"_config")
    
    base_main = BaseMain(**cfg)
    
    if cfg["mode"] == "train" :
        base_main.train(**cfg)
    elif cfg["mode"] == "infer" :
        base_main.infer(**cfg)
    
    