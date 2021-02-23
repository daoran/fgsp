#! /usr/bin/env python3

import rospy
import numpy as np
from pygsp import graphs, filters, reduction
from enum import Enum

import scipy.spatial
from sklearn.ensemble import RandomForestClassifier
from sklearn import metrics
from joblib import dump, load

class RandomForestPredictor(object):
    def __init__(self):
        if rospy.has_param('~random_forest_model'):
            random_forest_model = rospy.get_param("~random_forest_model")
        else:
            random_forest_model = "../config/forest.joblib"

        rospy.loginfo(f"[RandomForestPredictor] Loading model from {random_forest_model}")
        self.clf = load(random_forest_model)

    def predict(self, X):
        print(f"Prediction for: {X}")
        X = X.fillna(0)
        X = X.replace(float('inf'), 1)

        prediction = self.clf.predict(X)
        prediction_prob = self.clf.predict_proba(X)

        return (prediction, prediction_prob)
