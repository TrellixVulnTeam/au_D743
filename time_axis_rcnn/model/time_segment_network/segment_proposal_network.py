import chainer
from collections import defaultdict
import numpy as np
import config

from time_axis_rcnn.model.time_segment_network.proposal_creater import ProposalCreator
from time_axis_rcnn.model.time_segment_network.generate_anchors import get_all_anchors
import chainer.functions as F
import chainer.links as L
class SegmentProposalNetwork(chainer.Chain):

    def __init__(self,  mid_channels, n_anchors=len(config.ANCHOR_SIZE),
                 proposal_creator_params={},
                 initialW=None):
        super(SegmentProposalNetwork, self).__init__()
        self.proposal_layer = ProposalCreator(**proposal_creator_params)
        self.conv_layers = defaultdict(list)
        self.n_anchors = n_anchors
        with self.init_scope():
            # 下面的类同样要加group
            # score's channel 2 means 1/0  loc's channel 2 means x_min, x_max
            self.score = L.ConvolutionND(1, mid_channels, n_anchors * 2, 1, 1, 0,
                                                                                  initialW=initialW)
            self.loc = L.ConvolutionND(1, mid_channels, n_anchors * 2, 1, 1, 0,
                                                                                   initialW=initialW)



    def __call__(self, x, anchor_spatial_scale):
        n, _, ww = x.shape # Note that n is number of AU groups
        anchor = get_all_anchors(ww, stride=config.ANCHOR_STRIDE,
                                 sizes=np.array(config.ANCHOR_SIZE,dtype=np.float32) * anchor_spatial_scale)  # W, A, 2
        n_anchor = anchor.shape[1]
        assert n_anchor == self.n_anchors

        rpn_scores = self.score(x)  # 1, n_anchors * 2, w
        rpn_locs = self.loc(x)  # 1, n_anchors * 2, w

        # 1. transpose to (B, W, A * 2) then reshape to (B, W * A, 2)
        rpn_locs = rpn_locs.transpose((0, 2, 1)).reshape(n, -1,
                                                            2)  # put channel last dimension, then reshape to (B, W * A, 2) , 第二个维度每个都是一个anchor

        rpn_scores = rpn_scores.transpose(0, 2, 1)  # put channel last dimension shape = (B, W, C) C= A * 2
        rpn_scores = rpn_scores.reshape(n, -1, 2)  # shape (B, W * A, 2)
        return rpn_locs, rpn_scores, anchor  # 因为rpn_scores,所以RPN的loss是前景背景都要算
