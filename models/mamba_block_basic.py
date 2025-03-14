# Reference: https://github.com/state-spaces/mamba/blob/9127d1f47f367f5c9cc49c73ad73557089d02cb8/mamba_ssm/models/mixer_seq_simple.py

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn import init
from torch.nn.parameter import Parameter
from functools import partial
from einops import rearrange

from mamba_ssm.modules.mamba_simple import Mamba, Block
from mamba_ssm.models.mixer_seq_simple import _init_weights
from mamba_ssm.ops.triton.layernorm import RMSNorm

# github: https://github.com/state-spaces/mamba/blob/9127d1f47f367f5c9cc49c73ad73557089d02cb8/mamba_ssm/models/mixer_seq_simple.py
def create_block(
    d_model, cfg, layer_idx=0, rms_norm=True, fused_add_norm=False, residual_in_fp32=False, 
    ):
    d_state = cfg['model_cfg']['d_state'] # 16
    d_conv = cfg['model_cfg']['d_conv'] # 4
    expand = cfg['model_cfg']['expand'] # 4
    norm_epsilon = cfg['model_cfg']['norm_epsilon'] # 0.00001

    mixer_cls = partial(Mamba, layer_idx=layer_idx, d_state=d_state, d_conv=d_conv, expand=expand)
    norm_cls = partial(
        nn.LayerNorm if not rms_norm else RMSNorm, eps=norm_epsilon
    )
    block = Block(
            d_model,
            mixer_cls,
            norm_cls=norm_cls,
            fused_add_norm=fused_add_norm,
            residual_in_fp32=residual_in_fp32,
            )
    block.layer_idx = layer_idx
    return block

class MambaBlock_2(nn.Module):
    def __init__(self, in_channels, cfg):
        super(MambaBlock_2, self).__init__()
        n_layer = 1
        self.forward_blocks  = nn.ModuleList( create_block(in_channels, cfg) for i in range(n_layer) )

        self.apply(
            partial(
                _init_weights,
                n_layer=n_layer,
            )
        )

    def forward(self, x):
        x_forward = x.clone()
        resi_forward = None

        # Forward
        for layer in self.forward_blocks:
            x_forward, resi_forward = layer(x_forward, resi_forward)
        y_forward = (x_forward + resi_forward) if resi_forward is not None else x_forward

        return(y_forward)

class MambaBasic(nn.Module):
    """
    Temporal-Frequency Mamba block for sequence modeling.
    
    Attributes:
    cfg (Config): Configuration for the block.
    time_mamba (MambaBlock): Mamba block for temporal dimension.
    freq_mamba (MambaBlock): Mamba block for frequency dimension.
    tlinear (ConvTranspose1d): ConvTranspose1d layer for temporal dimension.
    flinear (ConvTranspose1d): ConvTranspose1d layer for frequency dimension.
    """
    def __init__(self, cfg):
        super(MambaBasic, self).__init__()
        self.cfg = cfg
        self.hid_feature = cfg['model_cfg']['hid_feature']
        
        # Initialize Mamba blocks
        #self.time_mamba = MambaBlock_2(in_channels=self.hid_feature, cfg=cfg)
        self.freq_mamba = MambaBlock_2(in_channels=self.hid_feature, cfg=cfg)
        
        # Initialize ConvTranspose1d layers
        #self.tlinear = nn.ConvTranspose1d(self.hid_feature * 2, self.hid_feature, 1, stride=1)
        self.flinear = nn.ConvTranspose1d(self.hid_feature, self.hid_feature, 1, stride=1)
    

    
    def forward(self, x):
        b,c, t, f = x.size()
        # Process through frequency Mamba block
        x = x.permute(0, 3, 2, 1).contiguous().view(f*b, t, c)
        x = self.flinear(self.freq_mamba(x).permute(0, 2, 1)).permute(0, 2, 1) + x

        # Reshape back to original form
        x = x.view(b, f, t, c).permute(0, 3, 2, 1).squeeze(1)  # (b, t, f)
        return x



