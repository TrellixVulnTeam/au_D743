import random

import chainer
import numpy as np
import os
from collections import defaultdict, OrderedDict

import config
from dataset_toolkit.compress_utils import get_zip_ROI_AU, get_AU_couple_child
from img_toolkit.face_mask_cropper import FaceMaskCropper


# obtain the cropped face image and bounding box and ground truth label for each box
class AUDataset(chainer.dataset.DatasetMixin):

    def __init__(self, database, fold, split_name, split_index, mc_manager, train_all_data, prefix="", pretrained_target=""):
        self.database = database
        self.split_name = split_name
        self.au_couple_dict = get_zip_ROI_AU()
        self.mc_manager = mc_manager
        self.au_couple_child_dict = get_AU_couple_child(self.au_couple_dict)
        self.AU_intensity_label = {}  # subject + "/" + emotion_seq + "/" + frame => ... not implemented
        self.pretrained_target = pretrained_target
        self.dir = config.DATA_PATH[database] # BP4D/DISFA/ BP4D_DISFA
        if train_all_data:
            id_list_file_path = os.path.join(self.dir + "/idx/{}_fold".format(fold),
                                             "full_pretrain.txt")
        else:
            id_list_file_path = os.path.join(self.dir + "/idx/{0}_fold{1}".format(fold, prefix),
                                             "id_{0}_{1}.txt".format(split_name, split_index))
        self.result_data = []

        print("idfile:{}".format(id_list_file_path))
        with open(id_list_file_path, "r") as file_obj:
            for idx, line in enumerate(file_obj):
                if line.rstrip():
                    line = line.rstrip()
                    img_path, au_set_str, _, current_database_name = line.split("\t")
                    AU_set = set(AU for AU in au_set_str.split(',') if AU in config.AU_ROI and AU in config.AU_SQUEEZE.inv)
                    if au_set_str == "0":
                        AU_set = set()
                    img_path = config.RGB_PATH[current_database_name] + os.sep + img_path  # id file 是相对路径
                    if os.path.exists(img_path):
                        self.result_data.append((img_path, AU_set, current_database_name))

        self.result_data.sort(key=lambda entry: (entry[0].split("/")[-3],entry[0].split("/")[-2],
                                                 int(entry[0].split("/")[-1][:entry[0].split("/")[-1].rindex(".")])))
        self._num_examples = len(self.result_data)
        print("read id file done, all examples:{}".format(self._num_examples))

    def __len__(self):
        return self._num_examples

    def assign_label(self, couple_box_dict, current_AU_couple, bbox, label):
        AU_couple_bin = dict()
        for au_couple_tuple, _ in couple_box_dict.items():
            # use connectivity components to seperate polygon
            AU_inside_box_set = current_AU_couple[au_couple_tuple]

            AU_bin = np.zeros(shape=len(config.AU_SQUEEZE), dtype=np.int32)  # 全0表示背景，脸上没有运动
            for AU in AU_inside_box_set:  # AU_inside_box_set may has -3 or ?3
                if AU not in config.AU_SQUEEZE.inv:
                    continue
                AU_squeeze = config.AU_SQUEEZE.inv[AU]  # AU_squeeze type = int
                np.put(AU_bin, AU_squeeze, 1)
            AU_couple_bin[au_couple_tuple] = AU_bin  # for the child
        # 循环两遍，第二遍拿出child_AU_couple
        for au_couple_tuple, box_list in couple_box_dict.items():
            AU_child_bin = np.zeros(shape=len(config.AU_SQUEEZE), dtype=np.int32)
            if au_couple_tuple in self.au_couple_child_dict:
                for au_couple_child in self.au_couple_child_dict[au_couple_tuple]:
                    AU_child_bin = np.bitwise_or(AU_child_bin, AU_couple_bin[au_couple_child])
            AU_bin_tmp = AU_couple_bin[au_couple_tuple]  # 全0表示背景，脸上没有运动
            AU_bin = np.bitwise_or(AU_child_bin, AU_bin_tmp)
            bbox.extend(box_list)
            for _ in box_list:
                label.append(AU_bin)

    def get_from_entry(self, img_path, AU_set, database_name):
        if not os.path.exists(img_path):
            raise IndexError("image file_path: {} not exist!".format(img_path))

        try:
            # print("begin fetch cropped image and bbox {}".format(img_path))

            key_prefix = self.database + "|"
            if self.pretrained_target is not None and len(self.pretrained_target) > 0:
                key_prefix = self.pretrained_target + "|"
            rgb_img_path = config.RGB_PATH[self.database] + os.path.sep + "/".join(img_path.split("/")[-3:])

            cropped_face, AU_box_dict = FaceMaskCropper.get_cropface_and_box(img_path,rgb_img_path,
                                                                             channel_first=True,
                                                                             mc_manager=self.mc_manager,
                                                                             key_prefix=key_prefix)

        except IndexError:
            # print("read image error:{}".format(read_img_path))
            # return AUDataset.get_example(self, i-1)  # 不得已为之
            raise IndexError("fetch crooped face and mask error:{} ! face landmark may not found.".format(img_path))

        non_AU_set = set()
        for AU in config.AU_ROI.keys():
            if AU not in AU_set and "?{}".format(AU) not in AU_set:
                non_AU_set.add("-{}".format(AU))
        unknown_AU_set = set()
        known_AU_set = set()
        for AU in AU_set:
            if AU.startswith("?"):
                unknown_AU_set.add(AU)
            else:
                known_AU_set.add(AU)
        all_AU_set = set()
        all_AU_set.update(non_AU_set)
        all_AU_set.update(unknown_AU_set)
        all_AU_set.update(known_AU_set)

        current_AU_couple = defaultdict(set)  # key = AU couple, value = AU 用于合并同一个区域的不同AU
        couple_box_dict = OrderedDict()  # key= AU couple

        # mask_path_dict's key AU maybe 3 or -2 or ?5
        for AU in all_AU_set:
            _AU = AU if AU.isdigit() else AU[1:]
            # print("AU:",AU,"_AU:",_AU)
            try:
                current_AU_couple[self.au_couple_dict[_AU]].add(
                    AU)  # value list may contain ?2 or -1, 所以这一步会把脸上有的，没有的AU都加上
            except KeyError:
                print(list(self.au_couple_dict.keys()), _AU)
                raise
        for AU, box_list in sorted(AU_box_dict.items(), key=lambda e: int(e[0])):
            _AU = AU if AU.isdigit() else AU[1:]
            if _AU in config.SYMMETRIC_AU and len(box_list) == 1:
                box_list.append(random.choice(box_list))
            couple_box_dict[self.au_couple_dict[_AU]] = box_list  # 所以这一步会把脸上有的，没有的AU都加上
        label = []  # one box may have multiple labels. so each entry is 10101110 binary code
        bbox = []  # AU = 0背景的box是随机取的
        self.assign_label(couple_box_dict, current_AU_couple, bbox, label)
        # print("assigned label over")
        assert len(bbox) > 0
        bbox = np.stack(bbox).astype(np.float32)
        label = np.stack(label).astype(np.int32)
        # bbox, label = self.proposal(bbox, label)  # 必须保证每个batch拿到的box数量一样
        assert bbox.shape[0] == label.shape[0]
        return cropped_face, bbox, label

    def get_example(self, i):
        '''
        Returns a color image and bounding boxes. The image is in CHW format.
        The returned image is RGB.

        :param i:  the index of the example
        :return: tuple of an image and its all bounding box
        '''
        if i > len(self.result_data):
            raise IndexError("Index too large")
        return self.get_from_entry(*self.result_data[i])