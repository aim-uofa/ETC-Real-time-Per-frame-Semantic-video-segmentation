import torch
from torch import nn
import torch.nn.functional as F

import model.resnet as models


class PPM(nn.Module):
    def __init__(self, in_dim, reduction_dim, bins, BatchNorm):
        super(PPM, self).__init__()
        self.features = []
        for bin in bins:
            self.features.append(nn.Sequential(
                nn.AdaptiveAvgPool2d(bin),
                nn.Conv2d(in_dim, reduction_dim, kernel_size=1, bias=False),
                BatchNorm(reduction_dim),
                nn.ReLU(inplace=True)
            ))
        self.features = nn.ModuleList(self.features)

    def forward(self, x):
        x_size = x.size()
        out = [x]
        for f in self.features:
            out.append(F.interpolate(f(x), x_size[2:], mode='bilinear', align_corners=True))
        return torch.cat(out, 1)


class PSPNet(nn.Module):
    def __init__(self, layers=18, bins=(1, 2, 3, 6), dropout=0.1, classes=2, zoom_factor=8, use_ppm=True,
                 criterion=nn.CrossEntropyLoss(ignore_index=255), BatchNorm=nn.BatchNorm2d, flow=False, sd=False,
                 pretrained=True):
        super(PSPNet, self).__init__()
        assert layers in [18, 50, 101, 152]
        assert 512 % len(bins) == 0
        assert classes > 1
        assert zoom_factor in [1, 2, 4, 8]
        self.zoom_factor = zoom_factor
        self.use_ppm = use_ppm
        self.flow = flow
        self.sd = sd
        self.criterion = criterion
        models.BatchNorm = BatchNorm

        if layers == 50:
            resnet = models.resnet50(pretrained=pretrained)
        elif layers == 18:
            resnet = models.resnet18(deep_base=False, pretrained=pretrained)
        elif layers == 101:
            resnet = models.resnet101(pretrained=pretrained)
        else:
            resnet = models.resnet152(pretrained=pretrained)
        self.layer0 = nn.Sequential(resnet.conv1, resnet.bn1, resnet.relu, resnet.maxpool)
        self.layer1, self.layer2, self.layer3, self.layer4 = resnet.layer1, resnet.layer2, resnet.layer3, resnet.layer4

        for n, m in self.layer3.named_modules():
            if 'conv2' in n:
                m.dilation, m.padding, m.stride = (2, 2), (2, 2), (1, 1)
            elif 'conv1' in n:
                m.stride = (1, 1)
            elif 'downsample.0' in n:
                m.stride = (1, 1)
        for n, m in self.layer4.named_modules():
            if 'conv2' in n:
                m.dilation, m.padding, m.stride = (4, 4), (4, 4), (1, 1)
            elif 'conv1' in n:
                m.stride = (1, 1)
            elif 'downsample.0' in n:
                m.stride = (1, 1)

        fea_dim = 512
        if use_ppm:
            self.ppm = PPM(fea_dim, int(fea_dim / len(bins)), bins, BatchNorm)
            fea_dim *= 2
        self.cls = nn.Sequential(
            nn.Conv2d(fea_dim, 256, kernel_size=3, padding=1, bias=False),
            BatchNorm(256),
            nn.ReLU(inplace=True),
            nn.Dropout2d(p=dropout),
            nn.Conv2d(256, classes, kernel_size=1)
        )
        if self.training:
            self.aux = nn.Sequential(
                nn.Conv2d(256, 256, kernel_size=3, padding=1, bias=False),
                BatchNorm(256),
                nn.ReLU(inplace=True),
                nn.Dropout2d(p=dropout),
                nn.Conv2d(256, classes, kernel_size=1)
            )

    def forward(self, x, y=None):
        x_size = x.size()
        # assert (x_size[2] - 1) % 8 == 0 and (x_size[3] - 1) % 8 == 0
        # h = int((x_size[2] - 1) / 8 * self.zoom_factor + 1)
        # w = int((x_size[3] - 1) / 8 * self.zoom_factor + 1)

        x = self.layer0(x)
        x = self.layer1(x)
        x = self.layer2(x)
        x_tmp = self.layer3(x)
        fea = self.layer4(x_tmp)
        if self.use_ppm:
            sd_fea = self.ppm(fea)
        x = self.cls(sd_fea)
        if self.flow:
            aux = self.aux(x_tmp)

            return {'logits': x, 'dsn': aux}
        elif self.sd:
            aux = self.aux(x_tmp)
            return {'logits': x, 'dsn': aux, 'fea': sd_fea}


        else:
            if self.zoom_factor != 1:
                x = F.interpolate(x, size=(x_size[2], x_size[3]), mode='bilinear', align_corners=True)

            if self.training:
                aux = self.aux(x_tmp)
                if self.zoom_factor != 1:
                    aux = F.interpolate(aux, size=(x_size[2], x_size[3]), mode='bilinear', align_corners=True)
                main_loss = self.criterion(x, y)
                aux_loss = self.criterion(aux, y)
                return x.max(1)[1], main_loss, aux_loss
            else:
                return x


if __name__ == '__main__':
    import os
    import time

    input = torch.rand(1, 3, 1024, 2048).cuda()
    model = PSPNet(layers=18, bins=(1, 2, 3, 6), dropout=0.1, classes=19, zoom_factor=1, use_ppm=True,
                   pretrained=False).cuda()

    print(model)
    #
    model_dict = model.state_dict()
    paras = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(paras)
    model.eval()
    print(model)
    sum_time = 0
    for i in range(20):
        input = torch.rand(1, 3, 720, 960).cuda()
        start = time.time()
        result = model(input)
        torch.cuda.synchronize()
        end = time.time()
        if i > 0:
            sum_time = sum_time + end - start
        # print(sum)
        print(end - start)
    avg = sum_time / 19
    print(avg)
    # output = model(input)
    # print('PSPNet', output['logits'].size())