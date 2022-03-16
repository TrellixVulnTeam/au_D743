import cv2

import chainer
import numpy as np
import config
from collections_toolkit.ordered_default_dict import DefaultOrderedDict
from img_toolkit.face_mask_cropper import FaceMaskCropper
from I3D_rcnn.datasets.AU_dataset import AUDataset
import random

class AU_video_dataset(chainer.dataset.DatasetMixin):

    def __init__(self, au_image_dataset:AUDataset,
                  sample_frame=10, train_mode=True, paper_report_label_idx=None):
        self.AU_image_dataset = au_image_dataset
        self.sample_frame = sample_frame
        self.train_mode = train_mode
        self.paper_report_label_idx = paper_report_label_idx
        self.class_num = len(config.AU_SQUEEZE)

        if self.paper_report_label_idx:
            self.class_num = len(self.paper_report_label_idx)

        self.seq_dict = DefaultOrderedDict(list)
        for idx, (img_path, *_) in enumerate(self.AU_image_dataset.result_data):
            video_seq_id = self.extract_sequence_key(img_path)
            self.seq_dict[video_seq_id].append(idx)

        self._order = None
        self.offsets = []
        self.result_data = []
        if self.train_mode:
            self.reset_for_train_mode()
        else:
            self.reset_for_test_mode()

    def __len__(self):
        return len(self.result_data)

    def reset_for_test_mode(self):
        assert self.train_mode is False
        self.offsets.clear()
        self.result_data.clear()
        T = self.sample_frame
        # jump_frame = random.randint(2, 5) #FIXME
        jump_frame = 2
        # if temporal_edge_mode == TemporalEdgeMode.no_temporal:
        #     for sequence_id, fetch_idx_lst in self.seq_dict.items():
        #         for i in range(0, len(fetch_idx_lst), T):
        #             self.offsets.append(fetch_idx_lst[i:i+T])
        #     for fetch_idx_lst in self.offsets:
        #         if len(fetch_idx_lst) < T:
        #             rest_pad_len = T - len(fetch_idx_lst)
        #             fetch_idx_lst = np.pad(fetch_idx_lst, (rest_pad_len, 0), 'edge')
        #         for fetch_id in fetch_idx_lst:
        #             self.result_data.append(self.AU_image_dataset.result_data[fetch_id])
        #     return

        for sequence_id, fetch_idx_lst in self.seq_dict.items():
            for start_offset in range(jump_frame):
                sub_idx_list = fetch_idx_lst[start_offset::jump_frame]
                for i in range(0, len(sub_idx_list)):
                    extended_list = sub_idx_list[i: i + T]  # highly overlap sequence, we only predict last frame of each sequence
                    if len(extended_list) == T:
                        self.offsets.append(extended_list)

            for j in range(1, T):
                self.offsets.append(fetch_idx_lst[0:j])
            for i in range(T, jump_frame * (T-1)):
                self.offsets.append(fetch_idx_lst[i-T:i])
        self._order = np.random.permutation(len(self.offsets))
        for order in self._order:
            fetch_idx_list = self.offsets[order]
            if len(fetch_idx_list) < T:
                rest_pad_len = T - len(fetch_idx_list)
                fetch_idx_list = np.pad(fetch_idx_list, (0, rest_pad_len), 'edge') # FIXME pad before first element
            assert len(fetch_idx_list) == T
            for fetch_id in fetch_idx_list:
                self.result_data.append(self.AU_image_dataset.result_data[fetch_id])


    def reset_for_train_mode(self):
        self.offsets.clear()
        assert self.train_mode
        T = self.sample_frame
        jump_frame = random.randint(4, 7)
        if T > 0:
            for sequence_id, fetch_idx_lst in self.seq_dict.items():
                for start_offset in range(jump_frame):
                    sub_idx_list = fetch_idx_lst[start_offset::jump_frame]
                    for i in range(0, len(sub_idx_list), T):
                        extended_list = sub_idx_list[i: i + T]
                        if len(extended_list) < T:
                            last_idx = extended_list[-1]
                            rest_list = list(filter(lambda e: e > last_idx, fetch_idx_lst))
                            if len(rest_list) > T - len(extended_list):
                                extended_list.extend(sorted(random.sample(rest_list, T - len(extended_list))))
                            else:
                                extended_list.extend(sorted(rest_list))
                        self.offsets.append(extended_list)

                # self.offsets.extend(
                #     [fetch_idx_lst[i:i + T] for i in range(0, len(fetch_idx_lst), T)])
        else:
            for sequence_id, fetch_idx_lst in self.seq_dict.items():
                self.offsets.append(fetch_idx_lst)

        self._order = np.random.permutation(len(self.offsets))
        previous_data_length = len(self.result_data)
        self.result_data.clear()
        for order in self._order:
            fetch_idx_list = self.offsets[order]

            if len(fetch_idx_list) < T:
                rest_pad_len = T - len(fetch_idx_list)
                fetch_idx_list = np.pad(fetch_idx_list, (0, rest_pad_len), 'edge')
            assert len(fetch_idx_list) == T
            for fetch_id in fetch_idx_list:
                self.result_data.append(self.AU_image_dataset.result_data[fetch_id])

        if previous_data_length != 0:
            if previous_data_length < len(self.result_data):
                assert (len(self.result_data) - previous_data_length) % T == 0
                del self.result_data[previous_data_length - len(self.result_data): ]
            elif previous_data_length > len(self.result_data):
                assert len(self.result_data) % T == 0
                assert (previous_data_length - len(self.result_data)) % T == 0
                chunks = [self.result_data[i:i + T] for i in range(0, len(self.result_data), T)]
                while previous_data_length > len(self.result_data):
                    self.result_data.extend(random.choice(chunks))
            assert len(self.result_data) == previous_data_length


    def extract_sequence_key(self, img_path):
        return "/".join((img_path.split("/")[-3], img_path.split("/")[-2]))


    def get_example(self, i):
        img_path, AU_set, database_name = self.result_data[i]

        # note that batch now is mix of T and batch_size, we must be reshape later
        try:
            cropped_face, bbox, label = self.AU_image_dataset.get_from_entry(img_path, AU_set, database_name)
            assert bbox.shape[0] == label.shape[0]
            if bbox.shape[0] != config.BOX_NUM[database_name]:
                print("found one error image: {0} box_number:{1}".format(img_path, bbox.shape[0]))
                bbox = bbox.tolist()
                label = label.tolist()

                if len(bbox) > config.BOX_NUM[database_name]:
                    all_del_idx = []
                    for idx, box in enumerate(bbox):
                        if FaceMaskCropper.calculate_area(*box) / float(config.IMG_SIZE[0] * config.IMG_SIZE[1]) < 0.01:
                            all_del_idx.append(idx)
                    for del_idx in all_del_idx:
                        del bbox[del_idx]
                        del label[del_idx]

                while len(bbox) < config.BOX_NUM[database_name]:
                    index = 0
                    bbox.insert(0, bbox[index])
                    label.insert(0, label[index])
                while len(bbox) > config.BOX_NUM[database_name]:
                    del bbox[-1]
                    del label[-1]


                bbox = np.stack(bbox)
                label = np.stack(label)
        except IndexError:
            print("image path : {} not get box".format(img_path))
            label = np.zeros(len(config.AU_SQUEEZE), dtype=np.int32)
            for AU in AU_set:
                np.put(label, config.AU_SQUEEZE.inv[AU], 1)
            if self.paper_report_label_idx:
                label = label[self.paper_report_label_idx]
            whole_image = np.transpose(cv2.resize(cv2.imread(img_path), config.IMG_SIZE), (2,0,1))
            whole_bbox = np.tile(np.array([1, 1, config.IMG_SIZE[1]-2, config.IMG_SIZE[0]-2], dtype=np.float32), (config.BOX_NUM[database_name], 1))
            whole_label = np.tile(label, (config.BOX_NUM[database_name], 1))
            return whole_image, whole_bbox, whole_label

        assert bbox.shape[0] == config.BOX_NUM[database_name], bbox.shape[0]
        if self.paper_report_label_idx:
            label = label[:, self.paper_report_label_idx]
        return cropped_face, bbox, label


