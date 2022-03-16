import chainer
import chainer.functions as F
import chainer.links as L
import numpy as np

from lstm_end_to_end.constants.enum_type import SpatialEdgeMode, TemporalEdgeMode
from lstm_end_to_end.model.roi_space_time_net.cubic_attention.cubic_attention_block import CubicMultiHeadAttention


class SpaceTimeCubicAttention(chainer.Chain):

    def __init__(self, n_heads, class_num, spatial_edge_mode: SpatialEdgeMode,
                 temporal_edge_mode: TemporalEdgeMode):
        super(SpaceTimeCubicAttention, self).__init__()
        self.class_num = class_num
        self.neg_pos_ratio = 3
        self.spatial_edge_mode = spatial_edge_mode
        self.temporal_edge_mode = temporal_edge_mode
        with self.init_scope():
            d_model = 1024 + 2048
            d_model_cubic = 256
            self.d_model_cubic = d_model_cubic
            roi_size = 14
            self.pointwise_conv_before = L.Convolution2D(in_channels=d_model, out_channels=d_model_cubic, ksize=1)
            if temporal_edge_mode != TemporalEdgeMode.no_temporal:
                self.temporal_cubic_attention = CubicMultiHeadAttention(n_heads=n_heads,
                                                                        d_model=d_model_cubic, height=roi_size, width=roi_size,
                                                                        d_k=d_model_cubic//n_heads,
                                                                        d_v=d_model_cubic//n_heads, bias=True, ksize=3,
                                                                        use_linear_project=False)
            if spatial_edge_mode != SpatialEdgeMode.no_edge:
                self.space_cubic_attention = CubicMultiHeadAttention(n_heads=n_heads, d_model=d_model_cubic, height=roi_size,
                                                                     width=roi_size,
                                                                     d_k=d_model_cubic//n_heads,
                                                                     d_v=d_model_cubic//n_heads,bias=True,
                                                                     ksize=3, use_linear_project=False)


            self.box_dim = 2048
            if spatial_edge_mode != SpatialEdgeMode.no_edge and temporal_edge_mode != TemporalEdgeMode.no_temporal:
                self.fc = L.Linear(14 * 14 * 256 * 2, self.box_dim)
            else:
                self.fc = L.Linear(14 * 14 * 256, self.box_dim)
            self.score_fc= L.Linear(self.box_dim, class_num)


    def forward(self, xs):
        # xs shape = (B, T, F, C, H ,W)
        batch_size, T, frame_box, channel, height, width = xs.shape
        xs = self.pointwise_conv_before(xs.reshape(xs.shape[0] * xs.shape[1] * xs.shape[2], xs.shape[3], xs.shape[4],
                                                  xs.shape[5]))
        xs = xs.reshape(batch_size, T, frame_box, self.d_model_cubic, height, width)

        if self.temporal_edge_mode != TemporalEdgeMode.no_temporal:
            temporal_input = F.transpose(xs, axes=(0, 2, 1, 3, 4, 5))  # B, F, T, C, H, W
            temporal_input = F.reshape(temporal_input, shape=(temporal_input.shape[0] * temporal_input.shape[1],
                                                              temporal_input.shape[2], temporal_input.shape[3],
                                                              temporal_input.shape[4], temporal_input.shape[5]))
            temporal_output = self.temporal_cubic_attention(temporal_input,temporal_input,temporal_input) # B*F, T, C, H, W. where C = hidden_dim


            # B, F, T, C', H, W
            temporal_output = F.reshape(temporal_output,
                                        shape=(xs.shape[0], xs.shape[2], xs.shape[1], temporal_output.shape[2],
                                               temporal_output.shape[3], temporal_output.shape[4]))
            temporal_output = F.transpose(temporal_output, axes=(0, 2, 1, 3, 4, 5))  # B, T, F, C', H, W
        if self.spatial_edge_mode != SpatialEdgeMode.no_edge:
            space_input = F.reshape(xs,
                                    shape=(xs.shape[0] * xs.shape[1], xs.shape[2], xs.shape[3], xs.shape[4], xs.shape[5]))

            space_output = self.space_cubic_attention(space_input, space_input, space_input) # B*T, F, C, H, W

            # B, T, F, C', H, W
            space_output = F.reshape(space_output, shape=(xs.shape[0], xs.shape[1], xs.shape[2], space_output.shape[2],
                                                          space_output.shape[3], space_output.shape[4]))

        if self.temporal_edge_mode!= TemporalEdgeMode.no_temporal and self.spatial_edge_mode!= SpatialEdgeMode.no_edge:
            fusion_output = F.concat((space_output, temporal_output), axis=3)  # B, T, F, 2C', H, W
        elif self.spatial_edge_mode != SpatialEdgeMode.no_edge:
            fusion_output = space_output
        elif self.temporal_edge_mode != TemporalEdgeMode.no_temporal:
            fusion_output = temporal_output
        fc_input = F.reshape(fusion_output,
                                   shape=(fusion_output.shape[0] * fusion_output.shape[1] * fusion_output.shape[2],
                                          -1))

        fc_output = self.fc(fc_input)
        fc_output = F.reshape(fc_output,
                                    shape=(fusion_output.shape[0], fusion_output.shape[1], fusion_output.shape[2],
                                           self.box_dim))  # B, T, F, 2048

        return fc_output


    def get_loss_index(self, pred, ts):
        union_gt = set()  # union of prediction positive and ground truth positive
        cpu_ts = chainer.cuda.to_cpu(ts)
        gt_pos_index = np.nonzero(cpu_ts)
        cpu_pred_score = (chainer.cuda.to_cpu(pred.data) > 0).astype(np.int32)
        pred_pos_index = np.nonzero(cpu_pred_score)
        len_gt_pos = len(gt_pos_index[0]) if len(gt_pos_index[0]) > 0 else 1
        neg_pick_count = self.neg_pos_ratio * len_gt_pos
        gt_pos_index_set = set(list(zip(*gt_pos_index)))
        pred_pos_index_set = set(list(zip(*pred_pos_index)))
        union_gt.update(gt_pos_index_set)
        union_gt.update(pred_pos_index_set)
        false_positive_index = np.asarray(list(pred_pos_index_set - gt_pos_index_set))  # shape = n x 2
        gt_pos_index_lst = list(gt_pos_index_set)
        if neg_pick_count <= len(false_positive_index):
            choice_fp = np.random.choice(np.arange(len(false_positive_index)), size=neg_pick_count, replace=False)
            gt_pos_index_lst.extend(list(map(tuple, false_positive_index[choice_fp].tolist())))
        else:
            gt_pos_index_lst.extend(list(map(tuple, false_positive_index.tolist())))
            rest_pick_count = neg_pick_count - len(false_positive_index)
            gt_neg_index = np.where(cpu_ts == 0)
            gt_neg_index_set = set(list(zip(*gt_neg_index)))
            gt_neg_index_set = gt_neg_index_set - set(gt_pos_index_lst)  # remove already picked
            gt_neg_index_array = np.asarray(list(gt_neg_index_set))
            choice_rest = np.random.choice(np.arange(len(gt_neg_index_array)), size=rest_pick_count, replace=True)
            gt_pos_index_lst.extend(list(map(tuple, gt_neg_index_array[choice_rest].tolist())))
        pick_index = list(zip(*gt_pos_index_lst))
        if len(union_gt) == 0:
            accuracy_pick_index = np.where(cpu_ts)
        else:
            accuracy_pick_index = list(zip(*union_gt))
        return pick_index, accuracy_pick_index


    def __call__(self, xs, labels):  # xs shape = B,T,F,C,H,W, labels=  (batch, T, F, D)
        assert xs.ndim == 6
        assert labels.ndim == 4
        fc_output = self.forward(xs) # B, T, F, 2048

        fc_output = F.reshape(fc_output, shape=(-1, self.box_dim))
        fc_output = self.score_fc(fc_output)  # B * T * F, class_num
        labels = self.xp.reshape(labels, (-1, self.class_num))
        pick_index, accuracy_pick_index = self.get_loss_index(fc_output, labels)
        loss = F.sigmoid_cross_entropy(fc_output[list(pick_index[0]), list(pick_index[1])],
                                       labels[list(pick_index[0]), list(pick_index[1])])
        accuracy = F.binary_accuracy(fc_output[list(accuracy_pick_index[0]), list(accuracy_pick_index[1])],
                                     labels[[list(accuracy_pick_index[0]), list(accuracy_pick_index[1])]])

        return loss, accuracy



