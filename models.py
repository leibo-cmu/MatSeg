import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision.models import vgg16


class PixelNet(torch.nn.Module):
    def __init__(self, n_classes):
        super(PixelNet, self).__init__()
        features = list(vgg16(pretrained=True).features)
        self.features = nn.ModuleList(features)
        self.linear = nn.Sequential(
            nn.Linear(1472, 2048, bias=True),
            nn.ReLU(inplace=True),
            nn.Dropout(),
            nn.Linear(2048, n_classes, bias=True),
        )
        self.is_train = False

    def set_train_flag(self, flag):
        self.is_train = flag

    def generate_rand_ind(self, labels, n_class, n_samples):
        n_samples_avg = int(n_samples / n_class)
        rand_ind = []
        for i in range(n_class):
            positions = np.where(labels.view(1, -1) == i)[1]
            if positions.size == 0:
                continue
            else:
                rand_ind.append(np.random.choice(positions, n_samples_avg))
        rand_ind = np.random.permutation(np.hstack(rand_ind))
        return rand_ind

    def set_rand_ind(self, rand_ind):
        self.rand_ind = rand_ind

    def forward(self, x):
        # take the feature maps before MaxPooling layers
        feature_maps_index = {3, 8, 15, 22, 29}
        if self.is_train:
            features = []
            size = (x.size(2), x.size(3))
            for ii, model in enumerate(self.features):
                x = model(x)
                if ii in feature_maps_index:
                    upsample = F.interpolate(x, size=size, mode='bilinear', align_corners=True)
                    upsample = upsample.view(upsample.size(0), upsample.size(1), -1)[:,:,self.rand_ind]
                    features.append(upsample)
            outputs = torch.cat(features, 1)
            outputs = outputs.permute(0, 2, 1)
            outputs = self.linear(outputs)
            outputs = outputs.permute(0, 2, 1)
        else:
            size, n_pixels = (x.size(2), x.size(3)), x.size(2)*x.size(3)
            upsample_maps = []
            for ii, model in enumerate(self.features):
                x = model(x)
                if ii in feature_maps_index:
                    upsample = F.interpolate(x, size=size, mode='bilinear', align_corners=True)
                    upsample_maps.append(upsample)
            # perform batch processing for fully connected layers to reduce memory
            outputs = []
            for ind in range(0, n_pixels, 10000):
                ind_range = range(ind, min(ind+10000, n_pixels))
                features = []
                for upsample in upsample_maps:
                    upsample = upsample.view(upsample.size(0), upsample.size(1), -1)[:, :, ind_range]
                    features.append(upsample)
                output = torch.cat(features, 1)
                output = output.permute(0, 2, 1)
                output = self.linear(output)
                output = output.permute(0, 2, 1)
                outputs.append(output)
            outputs = torch.cat(outputs, 2)
            outputs = outputs.reshape(*outputs.shape[:2], *size)
        return outputs


class UNet(torch.nn.Module):
    def __init__(self, n_classes):
        super(UNet, self).__init__()
        self.features = nn.ModuleList(list(vgg16(pretrained=True).features))
        self.conv1024 = self.double_conv(512, 1024)
        self.conv = nn.ModuleList([
            self.double_conv(1536, 512),
            self.double_conv(1024, 512),
            self.double_conv(768, 256),
            self.double_conv(384, 128),
            self.double_conv(192, 64),
        ])
        self.linear = nn.Sequential(
            nn.Linear(64, 256, bias=True),
            nn.ReLU(True),
            nn.Dropout(),
            nn.Linear(256, 256, bias=True),
            nn.ReLU(True),
            nn.Dropout(),
            nn.Linear(256, n_classes, bias=True),
        )

    def double_conv(self, in_channel, out_channel):
        net = nn.Sequential(
            nn.Conv2d(in_channel, out_channel, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_channel, out_channel, kernel_size=3, padding=1),
            nn.ReLU(inplace=True)
        )
        return net

    def forward(self, x):
        features = []
        for ii, model in enumerate(self.features):
            x = model(x)
            if ii in {3, 8, 15, 22, 29}:
                features.append(x)
        x = self.conv1024(x)
        for i in range(len(self.conv)):
            x = F.interpolate(x, size=(features[-1-i].size(2), features[-1-i].size(3)),
                              mode='bilinear', align_corners=True)
            x = torch.cat([x, features[-1-i]], 1)
            x = self.conv[i](x)
        x = x.permute(0, 2, 3, 1)
        x = self.linear(x)
        x = x.permute(0, 3, 1, 2)
        return x


class SegNet(torch.nn.Module):
    def __init__(self, n_classes):
        super(SegNet, self).__init__()
        features = nn.ModuleList(list(vgg16(pretrained=True).features))
        self.pool = [nn.MaxPool2d(kernel_size = 2, stride = 2, padding = 0, return_indices = True) for i in range(5)]
        self.unpool = [nn.MaxUnpool2d(kernel_size = 2, stride = 2, padding = 0) for i in range(5)]
        self.conv = nn.ModuleList([
            nn.Sequential(*features[0:4]),
            nn.Sequential(*features[5:9]),
            nn.Sequential(*features[10:16]),
            nn.Sequential(*features[17:23]),
            nn.Sequential(*features[24:30]),
            self.triple_conv(512, 512),
            self.triple_conv(512, 256),
            self.triple_conv(256, 128),
            self.double_conv(128, 64),
            self.double_conv(64, 64)
        ])
        self.linear = nn.Linear(64, n_classes)

    def conv_block(self, in_channel, out_channel):
        conv_block = nn.Sequential(
            nn.Conv2d(in_channel, out_channel, kernel_size=3, padding=1),
            nn.BatchNorm2d(out_channel),
            nn.ReLU(inplace=True)
        )
        return conv_block

    def double_conv(self, in_channel, out_channel):
        net = nn.Sequential(
            self.conv_block(in_channel, out_channel),
            self.conv_block(out_channel, out_channel),
        )
        return net

    def triple_conv(self, in_channel, out_channel):
        net = nn.Sequential(
            self.conv_block(in_channel, out_channel),
            self.conv_block(out_channel, out_channel),
            self.conv_block(out_channel, out_channel)
        )
        return net

    def forward(self, x):
        indices_list = []
        shape_list = []
        for i in range(5):
            shape_list.append(x.shape)
            x = self.conv[i](x)
            x, indices = self.pool[i](x)
            indices_list.append(indices)
        for i in range(5):
            x = self.unpool[i](x, indices_list[-1-i], output_size = shape_list[-1-i])
            x = self.conv[i+5](x)
        x = x.permute(0, 2, 3, 1)
        x = self.linear(x)
        x = x.permute(0, 3, 1, 2)
        return x


model_mappings = {
    'pixelnet': PixelNet,
    'unet': UNet,
    'segnet': SegNet
}