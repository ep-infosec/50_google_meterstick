# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for Operations."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import copy

from absl.testing import parameterized
from meterstick import metrics
from meterstick import operations
from meterstick import utils
import mock
import numpy as np
import pandas as pd
from pandas import testing
from scipy import stats
from sklearn import linear_model

import unittest


class DistributionTests(unittest.TestCase):

  df = pd.DataFrame({
      'X': [1, 1, 1, 5],
      'grp': ['A', 'A', 'B', 'B'],
      'country': ['US', 'US', 'US', 'EU']
  })
  sum_x = metrics.Sum('X')
  distribution = operations.Distribution('grp', sum_x)

  def test_distribution(self):
    output = self.distribution.compute_on(self.df)
    expected = pd.DataFrame({'Distribution of sum(X)': [0.25, 0.75]},
                            index=['A', 'B'])
    expected.index.name = 'grp'
    testing.assert_frame_equal(output, expected)

  def test_normalize(self):
    output = operations.Normalize('grp', self.sum_x).compute_on(self.df)
    expected = pd.DataFrame({'Distribution of sum(X)': [0.25, 0.75]},
                            index=['A', 'B'])
    expected.index.name = 'grp'
    testing.assert_frame_equal(output, expected)

  def test_distribution_over_multiple_columns(self):
    df = pd.DataFrame({
        'X': [2, 1, 1, 5],
        'grp': ['A', 'A', 'B', 'B'],
        'country': ['US', 'US', 'US', 'EU'],
        'platform': ['desktop', 'mobile', 'desktop', 'mobile']
    })
    sum_x = metrics.Sum('X')
    dist = operations.Distribution(['grp', 'platform'], sum_x)

    output = dist.compute_on(df, 'country')
    expected = pd.DataFrame({
        'Distribution of sum(X)': [1., 0.5, 0.25, 0.25],
        'country': ['EU', 'US', 'US', 'US'],
        'grp': ['B', 'A', 'A', 'B'],
        'platform': ['mobile', 'desktop', 'mobile', 'desktop']
    })
    expected.set_index(['country', 'grp', 'platform'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_distribution_melted(self):
    output = self.distribution.compute_on(self.df, melted=True)
    expected = pd.DataFrame({
        'Value': [0.25, 0.75],
        'grp': ['A', 'B'],
        'Metric': ['Distribution of sum(X)', 'Distribution of sum(X)']
    })
    expected.set_index(['Metric', 'grp'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_distribution_splitby(self):
    output = self.distribution.compute_on(self.df, 'country')
    expected = pd.DataFrame({
        'Distribution of sum(X)': [1., 2. / 3, 1. / 3],
        'grp': ['B', 'A', 'B'],
        'country': ['EU', 'US', 'US']
    })
    expected.set_index(['country', 'grp'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_distribution_splitby_melted(self):
    output = self.distribution.compute_on(self.df, 'country', melted=True)
    expected = pd.DataFrame({
        'Value': [1., 2. / 3, 1. / 3],
        'grp': ['B', 'A', 'B'],
        'Metric': ['Distribution of sum(X)'] * 3,
        'country': ['EU', 'US', 'US']
    })
    expected.set_index(['Metric', 'country', 'grp'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_distribution_splitby_multiple(self):
    df = pd.DataFrame({
        'X': [1, 1, 1, 5, 0, 1, 2, 3.5],
        'grp': ['A', 'A', 'B', 'B'] * 2,
        'country': ['US', 'US', 'US', 'EU'] * 2,
        'grp0': ['foo'] * 4 + ['bar'] * 4
    })
    output = self.distribution.compute_on(df, ['grp0', 'country'])
    bar = self.distribution.compute_on(df[df.grp0 == 'bar'], 'country')
    foo = self.distribution.compute_on(df[df.grp0 == 'foo'], 'country')
    expected = pd.concat([bar, foo], keys=['bar', 'foo'], names=['grp0'])
    testing.assert_frame_equal(output, expected)

  def test_distribution_multiple_metrics(self):
    metric = metrics.MetricList((self.sum_x, metrics.Count('X')))
    metric = operations.Distribution('grp', metric)
    output = metric.compute_on(self.df)
    expected = pd.DataFrame(
        {
            'Distribution of sum(X)': [0.25, 0.75],
            'Distribution of count(X)': [0.5, 0.5]
        },
        index=['A', 'B'],
        columns=['Distribution of sum(X)', 'Distribution of count(X)'])
    expected.index.name = 'grp'
    testing.assert_frame_equal(output, expected)

  def test_distribution_pipeline(self):
    output = self.sum_x | operations.Distribution('grp') | metrics.compute_on(
        self.df)
    expected = pd.DataFrame({'Distribution of sum(X)': [0.25, 0.75]},
                            index=['A', 'B'])
    expected.index.name = 'grp'
    testing.assert_frame_equal(output, expected)

  def test_distribution_cache_key(self):
    sum_x = metrics.Sum('X', 'X')
    metric = operations.Distribution('grp', sum_x)
    metric.compute_on(self.df, cache_key=42)
    testing.assert_series_equal(
        self.df.groupby('grp').X.sum(), sum_x.get_cached(42, 'grp'))
    self.assertTrue(metric.in_cache(42))

  def test_distribution_internal_caching_cleaned_up(self):
    sum_x = metrics.Sum('X')
    m = operations.Distribution('grp', sum_x)
    m.compute_on(self.df)
    self.assertEqual(m.cache, {})
    self.assertEqual(sum_x.cache, {})
    self.assertIsNone(sum_x.cache_key)
    self.assertIsNone(m.cache_key)

  def test_distribution_with_jackknife_internal_caching_cleaned_up(self):
    df = pd.DataFrame({
        'X': [1, 1, 1, 5],
        'grp': ['A', 'A', 'B', 'B'],
        'country': ['US', 'US', 'US', 'EU'],
        'cookie': [1, 2, 1, 2]
    })
    sum_x = metrics.Sum('X')
    m = operations.Distribution('grp', sum_x)
    jk = operations.Jackknife('cookie', m)
    jk.compute_on(df)
    self.assertEqual(jk.cache, {})
    self.assertEqual(m.cache, {})
    self.assertEqual(sum_x.cache, {})
    self.assertIsNone(jk.cache_key)
    self.assertIsNone(m.cache_key)
    self.assertIsNone(sum_x.cache_key)


class CumulativeDistributionTests(unittest.TestCase):

  df = pd.DataFrame({
      'X': [1, 1, 1, 5],
      'grp': ['B', 'B', 'A', 'A'],
      'country': ['US', 'US', 'US', 'EU']
  })
  sum_x = metrics.Sum('X')
  metric = operations.CumulativeDistribution('grp', sum_x)

  def test_cumulative_distribution(self):
    output = self.metric.compute_on(self.df)
    expected = pd.DataFrame({'Cumulative Distribution of sum(X)': [0.75, 1.]},
                            index=['A', 'B'])
    expected.index.name = 'grp'
    testing.assert_frame_equal(output, expected)

  def test_cumulative_distribution_over_multiple_columns(self):
    df = pd.DataFrame({
        'X': [2, 1, 1, 5],
        'grp': ['A', 'A', 'B', 'B'],
        'country': ['US', 'US', 'US', 'EU'],
        'platform': ['desktop', 'mobile', 'desktop', 'mobile']
    })
    sum_x = metrics.Sum('X')
    cum_dict = operations.CumulativeDistribution(['grp', 'platform'], sum_x)

    output = cum_dict.compute_on(df, 'country')
    expected = pd.DataFrame({
        'Cumulative Distribution of sum(X)': [1., 0.5, 0.75, 1],
        'country': ['EU', 'US', 'US', 'US'],
        'grp': ['B', 'A', 'A', 'B'],
        'platform': ['mobile', 'desktop', 'mobile', 'desktop']
    })
    expected.set_index(['country', 'grp', 'platform'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_cumulative_distribution_melted(self):
    output = self.metric.compute_on(self.df, melted=True)
    expected = pd.DataFrame({
        'Value': [0.75, 1.],
        'grp': ['A', 'B'],
        'Metric': ['Cumulative Distribution of sum(X)'] * 2
    })
    expected.set_index(['Metric', 'grp'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_cumulative_distribution_splitby(self):
    output = self.metric.compute_on(self.df, 'country')
    expected = pd.DataFrame({
        'Cumulative Distribution of sum(X)': [1., 1. / 3, 1.],
        'grp': ['A', 'A', 'B'],
        'country': ['EU', 'US', 'US']
    })
    expected.set_index(['country', 'grp'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_cumulative_distribution_splitby_melted(self):
    output = self.metric.compute_on(self.df, 'country', melted=True)
    expected = pd.DataFrame({
        'Value': [1., 1. / 3, 1.],
        'grp': ['A', 'A', 'B'],
        'Metric': ['Cumulative Distribution of sum(X)'] * 3,
        'country': ['EU', 'US', 'US']
    })
    expected.set_index(['Metric', 'country', 'grp'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_cumulative_distribution_splitby_multiple(self):
    df = pd.DataFrame({
        'X': [1, 1, 1, 5, 0, 2, 1.5, 3],
        'grp': ['B', 'B', 'A', 'A'] * 2,
        'country': ['US', 'US', 'US', 'EU'] * 2,
        'grp0': ['foo'] * 4 + ['bar'] * 4
    })
    output = self.metric.compute_on(df, ['grp0', 'country'])
    output.sort_index(level=['grp0', 'grp', 'country'], inplace=True)
    bar = self.metric.compute_on(df[df.grp0 == 'bar'], 'country')
    foo = self.metric.compute_on(df[df.grp0 == 'foo'], 'country')
    expected = pd.concat([bar, foo], keys=['bar', 'foo'], names=['grp0'])
    expected = expected.sort_index(level=['grp0', 'grp', 'country'])
    testing.assert_frame_equal(output, expected)

  def test_cumulative_distribution_order(self):
    metric = operations.CumulativeDistribution('grp', self.sum_x, ('B', 'A'))
    output = metric.compute_on(self.df)
    expected = pd.DataFrame({'Cumulative Distribution of sum(X)': [0.25, 1.]},
                            index=['B', 'A'])
    expected.index.name = 'grp'
    testing.assert_frame_equal(output, expected)

  def test_cumulative_distribution_ascending(self):
    metric = operations.CumulativeDistribution(
        'grp', self.sum_x, ascending=False)
    output = metric.compute_on(self.df)
    expected = pd.DataFrame({'Cumulative Distribution of sum(X)': [0.25, 1.]},
                            index=['B', 'A'])
    expected.index.name = 'grp'
    testing.assert_frame_equal(output, expected)

  def test_cumulative_distribution_order_splitby(self):
    metric = operations.CumulativeDistribution('grp', self.sum_x, ('B', 'A'))
    output = metric.compute_on(self.df, 'country')
    expected = pd.DataFrame({
        'Cumulative Distribution of sum(X)': [1., 2. / 3, 1.],
        'grp': ['A', 'B', 'A'],
        'country': ['EU', 'US', 'US']
    })
    expected.set_index(['country', 'grp'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_cumulative_distribution_multiple_metrics(self):
    metric = metrics.MetricList((self.sum_x, metrics.Count('X')))
    metric = operations.CumulativeDistribution('grp', metric)
    output = metric.compute_on(self.df)
    expected = pd.DataFrame(
        {
            'Cumulative Distribution of sum(X)': [0.75, 1.],
            'Cumulative Distribution of count(X)': [0.5, 1.]
        },
        index=['A', 'B'],
        columns=[
            'Cumulative Distribution of sum(X)',
            'Cumulative Distribution of count(X)'
        ])
    expected.index.name = 'grp'
    testing.assert_frame_equal(output, expected)

  def test_cumulative_distribution_pipeline(self):
    output = self.sum_x | operations.CumulativeDistribution(
        'grp') | metrics.compute_on(self.df)
    expected = pd.DataFrame({'Cumulative Distribution of sum(X)': [0.75, 1.]},
                            index=['A', 'B'])
    expected.index.name = 'grp'
    testing.assert_frame_equal(output, expected)

  def test_cumulative_distribution_cache_key(self):
    sum_x = metrics.Sum('X', 'X')
    metric = operations.CumulativeDistribution('grp', sum_x)
    metric.compute_on(self.df, cache_key=42)
    testing.assert_series_equal(
        self.df.groupby('grp').X.sum(), sum_x.get_cached(42, 'grp'))
    self.assertTrue(metric.in_cache(42))

  def test_cumulative_distribution_internal_caching_cleaned_up(self):
    sum_x = metrics.Sum('X')
    m = operations.CumulativeDistribution('grp', sum_x)
    m.compute_on(self.df)
    self.assertEqual(m.cache, {})
    self.assertEqual(sum_x.cache, {})
    self.assertIsNone(sum_x.cache_key)
    self.assertIsNone(m.cache_key)

  def test_cumulative_distribution_with_jackknife_internal_caching_cleaned_up(
      self):
    df = pd.DataFrame({
        'X': [1, 1, 1, 5],
        'grp': ['B', 'B', 'A', 'A'],
        'country': ['US', 'US', 'US', 'EU'],
        'cookie': [1, 2, 1, 2]
    })
    sum_x = metrics.Sum('X')
    m = operations.CumulativeDistribution('grp', sum_x)
    jk = operations.Jackknife('cookie', m)
    jk.compute_on(df)
    self.assertEqual(jk.cache, {})
    self.assertEqual(m.cache, {})
    self.assertEqual(sum_x.cache, {})
    self.assertIsNone(jk.cache_key)
    self.assertIsNone(m.cache_key)
    self.assertIsNone(sum_x.cache_key)


class PercentChangeTests(unittest.TestCase):

  df = pd.DataFrame({
      'X': [1, 2, 3, 4, 5, 6],
      'Condition': [0, 0, 0, 1, 1, 1],
      'grp': ['A', 'A', 'B', 'A', 'B', 'C']
  })
  metric_lst = metrics.MetricList((metrics.Sum('X'), metrics.Count('X')))

  def test_percent_change(self):
    metric = operations.PercentChange('Condition', 0, self.metric_lst)
    output = metric.compute_on(self.df)
    expected = pd.DataFrame(
        [[150., 0.]],
        columns=['sum(X) Percent Change', 'count(X) Percent Change'],
        index=[1])
    expected.index.name = 'Condition'
    testing.assert_frame_equal(output, expected)

  def test_percent_change_include_baseline(self):
    metric = operations.PercentChange('Condition', 0, self.metric_lst, True)
    output = metric.compute_on(self.df)
    expected = pd.DataFrame(
        [[0., 0.], [150., 0.]],
        columns=['sum(X) Percent Change', 'count(X) Percent Change'],
        index=[0, 1])
    expected.index.name = 'Condition'
    testing.assert_frame_equal(output, expected)

  def test_percent_change_melted(self):
    metric = operations.PercentChange('Condition', 0, self.metric_lst)
    output = metric.compute_on(self.df, melted=True)
    expected = pd.DataFrame({
        'Value': [150., 0.],
        'Metric': ['sum(X) Percent Change', 'count(X) Percent Change'],
        'Condition': [1, 1]
    })
    expected.set_index(['Metric', 'Condition'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_percent_change_melted_include_baseline(self):
    metric = operations.PercentChange('Condition', 0, self.metric_lst, True)
    output = metric.compute_on(self.df, melted=True)
    expected = pd.DataFrame({
        'Value': [0., 150., 0., 0.],
        'Metric': [
            'sum(X) Percent Change', 'sum(X) Percent Change',
            'count(X) Percent Change', 'count(X) Percent Change'
        ],
        'Condition': [0, 1, 0, 1]
    })
    expected.set_index(['Metric', 'Condition'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_percent_change_splitby(self):
    metric = operations.PercentChange('Condition', 0, self.metric_lst, True)
    output = metric.compute_on(self.df, 'grp')
    expected = pd.DataFrame(
        {
            'sum(X) Percent Change': [0., 100. / 3, 0., 200. / 3, np.nan],
            'count(X) Percent Change': [0., -50., 0., 0., np.nan],
            'Condition': [0, 1, 0, 1, 1],
            'grp': ['A', 'A', 'B', 'B', 'C']
        },
        columns=[
            'sum(X) Percent Change', 'count(X) Percent Change', 'Condition',
            'grp'
        ])
    expected.set_index(['grp', 'Condition'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_percent_change_splitby_melted(self):
    metric = operations.PercentChange('Condition', 0, self.metric_lst, True)
    output = metric.compute_on(self.df, 'grp', melted=True)
    expected = pd.DataFrame({
        'Value': [0., 100. / 3, 0., 200. / 3, np.nan, 0., -50., 0., 0., np.nan],
        'Metric': ['sum(X) Percent Change'] * 5 +
                  ['count(X) Percent Change'] * 5,
        'Condition': [0, 1, 0, 1, 1] * 2,
        'grp': ['A', 'A', 'B', 'B', 'C'] * 2
    })
    expected.set_index(['Metric', 'grp', 'Condition'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_percent_change_splitby_multiple(self):
    df = pd.DataFrame({
        'X': [1, 2, 3, 4, 5, 6, 1.2, 2.2, 3.2, 4.2, 5.2, 6.5],
        'Condition': [0, 0, 0, 1, 1, 1] * 2,
        'grp': ['A', 'A', 'B', 'A', 'B', 'C'] * 2,
        'grp0': ['foo'] * 6 + ['bar'] * 6
    })
    metric = operations.PercentChange('Condition', 0, self.metric_lst, True)
    output = metric.compute_on(df, ['grp0', 'grp'])
    bar = metric.compute_on(df[df.grp0 == 'bar'], 'grp')
    foo = metric.compute_on(df[df.grp0 == 'foo'], 'grp')
    expected = pd.concat([bar, foo], keys=['bar', 'foo'], names=['grp0'])
    expected.sort_index(level=['grp0', 'grp'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_percent_change_multiple_condition_columns(self):
    df = self.df.copy()
    metric = operations.PercentChange(['Condition', 'grp'], (0, 'A'),
                                      self.metric_lst)
    output = metric.compute_on(df)
    df['Condition_and_grp'] = df[['Condition', 'grp']].apply(tuple, 1)
    expected_metric = operations.PercentChange('Condition_and_grp', (0, 'A'),
                                               self.metric_lst)
    expected = expected_metric.compute_on(df)
    expected = pd.DataFrame(
        expected.values, index=output.index, columns=output.columns)
    testing.assert_frame_equal(output, expected)

  def test_percent_change_multiple_condition_columns_include_baseline(self):
    df = self.df.copy()
    metric = operations.PercentChange(['Condition', 'grp'], (0, 'A'),
                                      self.metric_lst, True)
    output = metric.compute_on(df)
    df['Condition_and_grp'] = df[['Condition', 'grp']].apply(tuple, 1)
    expected_metric = operations.PercentChange('Condition_and_grp', (0, 'A'),
                                               self.metric_lst, True)
    expected = expected_metric.compute_on(df)
    expected = pd.DataFrame(
        expected.values, index=output.index, columns=output.columns)
    testing.assert_frame_equal(output, expected)

  def test_percent_change_multiple_condition_columns_splitby(self):
    df = pd.DataFrame({
        'X': [1, 2, 3, 4, 5, 6],
        'Condition': [0, 0, 0, 1, 1, 1],
        'grp': ['A', 'A', 'B', 'A', 'B', 'B'],
        'grp2': ['foo', 'bar', 'foo', 'bar', 'foo', 'bar']
    })
    metric = operations.PercentChange(['Condition', 'grp'], (0, 'A'),
                                      self.metric_lst)
    output = metric.compute_on(df, 'grp2')
    df['Condition_and_grp'] = df[['Condition', 'grp']].apply(tuple, 1)
    expected_metric = operations.PercentChange('Condition_and_grp', (0, 'A'),
                                               self.metric_lst)
    expected = expected_metric.compute_on(df, 'grp2')
    expected = pd.DataFrame(
        expected.values, index=output.index, columns=output.columns)
    testing.assert_frame_equal(output, expected)

  def test_percent_change_multiple_condition_columns_include_baseline_splitby(
      self):
    df = pd.DataFrame({
        'X': [1, 2, 3, 4, 5, 6],
        'Condition': [0, 0, 0, 1, 1, 1],
        'grp': ['A', 'A', 'B', 'A', 'B', 'B'],
        'grp2': ['foo', 'bar', 'foo', 'bar', 'foo', 'bar']
    })
    metric = operations.PercentChange(['Condition', 'grp'], (0, 'A'),
                                      self.metric_lst, True)
    output = metric.compute_on(df, 'grp2')
    df['Condition_and_grp'] = df[['Condition', 'grp']].apply(tuple, 1)
    expected_metric = operations.PercentChange('Condition_and_grp', (0, 'A'),
                                               self.metric_lst, True)
    expected = expected_metric.compute_on(df, 'grp2')
    expected = pd.DataFrame(
        expected.values, index=output.index, columns=output.columns)
    testing.assert_frame_equal(output, expected)

  def test_percent_change_pipeline(self):
    metric = operations.PercentChange('Condition', 0)
    output = self.metric_lst | metric | metrics.compute_on(self.df)
    expected = pd.DataFrame(
        [[150., 0.]],
        columns=['sum(X) Percent Change', 'count(X) Percent Change'],
        index=[1])
    expected.index.name = 'Condition'
    testing.assert_frame_equal(output, expected)

  def test_percent_change_cache_key(self):
    sum_x = metrics.Sum('X', 'X')
    metric = operations.PercentChange('Condition', 0, sum_x)
    metric.compute_on(self.df, cache_key=42)
    testing.assert_series_equal(
        self.df.groupby('Condition').X.sum(), sum_x.get_cached(42, 'Condition'))
    self.assertTrue(metric.in_cache(42))

  def test_percent_change_internal_caching_cleaned_up(self):
    sum_x = metrics.Sum('X')
    m = operations.PercentChange('Condition', 0, sum_x)
    m.compute_on(self.df)
    self.assertEqual(m.cache, {})
    self.assertEqual(sum_x.cache, {})
    self.assertIsNone(sum_x.cache_key)
    self.assertIsNone(m.cache_key)

  def test_percent_change_with_jackknife_internal_caching_cleaned_up(self):
    df = pd.DataFrame({
        'X': [1, 2, 3, 4, 5, 6],
        'Condition': [0, 0, 0, 1, 1, 1],
        'grp': ['A', 'A', 'B', 'A', 'B', 'C'],
        'cookie': [1, 2, 3] * 2
    })
    sum_x = metrics.Sum('X')
    m = operations.PercentChange('Condition', 0, sum_x)
    jk = operations.Jackknife('cookie', m)
    jk.compute_on(df)
    self.assertEqual(jk.cache, {})
    self.assertEqual(m.cache, {})
    self.assertEqual(sum_x.cache, {})
    self.assertIsNone(jk.cache_key)
    self.assertIsNone(m.cache_key)
    self.assertIsNone(sum_x.cache_key)


class AbsoluteChangeTests(unittest.TestCase):

  df = pd.DataFrame({
      'X': [1, 2, 3, 4, 5, 6],
      'Condition': [0, 0, 0, 1, 1, 1],
      'grp': ['A', 'A', 'B', 'A', 'B', 'C']
  })
  metric_lst = metrics.MetricList((metrics.Sum('X'), metrics.Count('X')))

  def test_absolute_change(self):
    metric = operations.AbsoluteChange('Condition', 0, self.metric_lst)
    output = metric.compute_on(self.df)
    expected = pd.DataFrame(
        [[9, 0]],
        columns=['sum(X) Absolute Change', 'count(X) Absolute Change'],
        index=[1])
    expected.index.name = 'Condition'
    testing.assert_frame_equal(output, expected)

  def test_absolute_change_include_baseline(self):
    metric = operations.AbsoluteChange('Condition', 0, self.metric_lst, True)
    output = metric.compute_on(self.df)
    expected = pd.DataFrame(
        [[0, 0], [9, 0]],
        columns=['sum(X) Absolute Change', 'count(X) Absolute Change'],
        index=[0, 1])
    expected.index.name = 'Condition'
    testing.assert_frame_equal(output, expected)

  def test_absolute_change_melted(self):
    metric = operations.AbsoluteChange('Condition', 0, self.metric_lst)
    output = metric.compute_on(self.df, melted=True)
    expected = pd.DataFrame({
        'Value': [9, 0],
        'Metric': ['sum(X) Absolute Change', 'count(X) Absolute Change'],
        'Condition': [1, 1]
    })
    expected.set_index(['Metric', 'Condition'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_absolute_change_melted_include_baseline(self):
    metric = operations.AbsoluteChange('Condition', 0, self.metric_lst, True)
    output = metric.compute_on(self.df, melted=True)
    expected = pd.DataFrame({
        'Value': [0, 9, 0, 0],
        'Metric': [
            'sum(X) Absolute Change', 'sum(X) Absolute Change',
            'count(X) Absolute Change', 'count(X) Absolute Change'
        ],
        'Condition': [0, 1, 0, 1]
    })
    expected.set_index(['Metric', 'Condition'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_absolute_change_splitby(self):
    metric = operations.AbsoluteChange('Condition', 0, self.metric_lst, True)
    output = metric.compute_on(self.df, 'grp')
    expected = pd.DataFrame(
        {
            'sum(X) Absolute Change': [0., 1., 0., 2., np.nan],
            'count(X) Absolute Change': [0., -1., 0., 0., np.nan],
            'Condition': [0, 1, 0, 1, 1],
            'grp': ['A', 'A', 'B', 'B', 'C']
        },
        columns=[
            'sum(X) Absolute Change', 'count(X) Absolute Change', 'Condition',
            'grp'
        ])
    expected.set_index(['grp', 'Condition'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_absolute_change_splitby_melted(self):
    metric = operations.AbsoluteChange('Condition', 0, self.metric_lst, True)
    output = metric.compute_on(self.df, 'grp', melted=True)
    expected = pd.DataFrame({
        'Value': [0., 1., 0., 2., np.nan, 0., -1., 0., 0., np.nan],
        'Metric': ['sum(X) Absolute Change'] * 5 +
                  ['count(X) Absolute Change'] * 5,
        'Condition': [0, 1, 0, 1, 1] * 2,
        'grp': ['A', 'A', 'B', 'B', 'C'] * 2
    })
    expected.set_index(['Metric', 'grp', 'Condition'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_absolute_change_splitby_multiple(self):
    df = pd.DataFrame({
        'X': [1, 2, 3, 4, 5, 6, 1.2, 2.2, 3.2, 4.2, 5.2, 6.5],
        'Condition': [0, 0, 0, 1, 1, 1] * 2,
        'grp': ['A', 'A', 'B', 'A', 'B', 'C'] * 2,
        'grp0': ['foo'] * 6 + ['bar'] * 6
    })
    metric = operations.AbsoluteChange('Condition', 0, self.metric_lst, True)
    output = metric.compute_on(df, ['grp0', 'grp'])
    bar = metric.compute_on(df[df.grp0 == 'bar'], 'grp')
    foo = metric.compute_on(df[df.grp0 == 'foo'], 'grp')
    expected = pd.concat([bar, foo], keys=['bar', 'foo'], names=['grp0'])
    expected.sort_index(level=['grp0', 'grp'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_absolute_change_multiple_condition_columns(self):
    df = self.df.copy()
    metric = operations.AbsoluteChange(['Condition', 'grp'], (0, 'A'),
                                       self.metric_lst)
    output = metric.compute_on(df)
    df['Condition_and_grp'] = df[['Condition', 'grp']].apply(tuple, 1)
    expected_metric = operations.AbsoluteChange('Condition_and_grp', (0, 'A'),
                                                self.metric_lst)
    expected = expected_metric.compute_on(df)
    expected = pd.DataFrame(
        expected.values, index=output.index, columns=output.columns)
    testing.assert_frame_equal(output, expected)

  def test_absolute_change_multiple_condition_columns_include_baseline(self):
    df = self.df.copy()
    metric = operations.AbsoluteChange(['Condition', 'grp'], (0, 'A'),
                                       self.metric_lst, True)
    output = metric.compute_on(df)
    df['Condition_and_grp'] = df[['Condition', 'grp']].apply(tuple, 1)
    expected_metric = operations.AbsoluteChange('Condition_and_grp', (0, 'A'),
                                                self.metric_lst, True)
    expected = expected_metric.compute_on(df)
    expected = pd.DataFrame(
        expected.values, index=output.index, columns=output.columns)
    testing.assert_frame_equal(output, expected)

  def test_absolute_change_multiple_condition_columns_splitby(self):
    df = pd.DataFrame({
        'X': [1, 2, 3, 4, 5, 6],
        'Condition': [0, 0, 0, 1, 1, 1],
        'grp': ['A', 'A', 'B', 'A', 'B', 'B'],
        'grp2': ['foo', 'bar', 'foo', 'bar', 'foo', 'bar']
    })
    metric = operations.AbsoluteChange(['Condition', 'grp'], (0, 'A'),
                                       self.metric_lst)
    output = metric.compute_on(df, 'grp2')
    df['Condition_and_grp'] = df[['Condition', 'grp']].apply(tuple, 1)
    expected_metric = operations.AbsoluteChange('Condition_and_grp', (0, 'A'),
                                                self.metric_lst)
    expected = expected_metric.compute_on(df, 'grp2')
    expected = pd.DataFrame(
        expected.values, index=output.index, columns=output.columns)
    testing.assert_frame_equal(output, expected)

  def test_absolute_change_multiple_condition_columns_include_baseline_splitby(
      self):
    df = pd.DataFrame({
        'X': [1, 2, 3, 4, 5, 6],
        'Condition': [0, 0, 0, 1, 1, 1],
        'grp': ['A', 'A', 'B', 'A', 'B', 'B'],
        'grp2': ['foo', 'bar', 'foo', 'bar', 'foo', 'bar']
    })
    metric = operations.AbsoluteChange(['Condition', 'grp'], (0, 'A'),
                                       self.metric_lst, True)
    output = metric.compute_on(df, 'grp2')
    df['Condition_and_grp'] = df[['Condition', 'grp']].apply(tuple, 1)
    expected_metric = operations.AbsoluteChange('Condition_and_grp', (0, 'A'),
                                                self.metric_lst, True)
    expected = expected_metric.compute_on(df, 'grp2')
    expected = pd.DataFrame(
        expected.values, index=output.index, columns=output.columns)
    testing.assert_frame_equal(output, expected)

  def test_absolute_change_pipeline(self):
    metric = operations.AbsoluteChange('Condition', 0)
    output = self.metric_lst | metric | metrics.compute_on(self.df)
    expected = pd.DataFrame(
        [[9, 0]],
        columns=['sum(X) Absolute Change', 'count(X) Absolute Change'],
        index=[1])
    expected.index.name = 'Condition'
    testing.assert_frame_equal(output, expected)

  def test_absolute_change_internal_caching(self):
    sum_x = metrics.Sum('X')
    pct = operations.PercentChange('Condition', 0, sum_x)
    ab = operations.AbsoluteChange('Condition', 0, sum_x)
    metric = metrics.MetricList((pct, ab))
    with mock.patch.object(
        sum_x, 'compute_through', wraps=sum_x.compute_through) as mock_fn:
      metric.compute_on(self.df)
      mock_fn.assert_called_once()

  def test_absolute_change_cache_key(self):
    sum_x = metrics.Sum('X', 'X')
    metric = operations.AbsoluteChange('Condition', 0, sum_x)
    metric.compute_on(self.df, cache_key=42)
    testing.assert_series_equal(
        self.df.groupby('Condition').X.sum(), sum_x.get_cached(42, 'Condition'))
    self.assertTrue(metric.in_cache(42))

  def test_absolute_change_internal_caching_cleaned_up(self):
    sum_x = metrics.Sum('X')
    m = operations.AbsoluteChange('Condition', 0, sum_x)
    m.compute_on(self.df)
    self.assertEqual(m.cache, {})
    self.assertEqual(sum_x.cache, {})
    self.assertIsNone(sum_x.cache_key)
    self.assertIsNone(m.cache_key)

  def test_absolute_change_with_jackknife_internal_caching_cleaned_up(self):
    df = pd.DataFrame({
        'X': [1, 2, 3, 4, 5, 6],
        'Condition': [0, 0, 0, 1, 1, 1],
        'grp': ['A', 'A', 'B', 'A', 'B', 'C'],
        'cookie': [1, 2, 3] * 2
    })
    sum_x = metrics.Sum('X')
    m = operations.AbsoluteChange('Condition', 0, sum_x)
    jk = operations.Jackknife('cookie', m)
    jk.compute_on(df)
    self.assertEqual(jk.cache, {})
    self.assertEqual(m.cache, {})
    self.assertEqual(sum_x.cache, {})
    self.assertIsNone(jk.cache_key)
    self.assertIsNone(m.cache_key)
    self.assertIsNone(sum_x.cache_key)


class PrePostChangeTests(unittest.TestCase):

  n = 40
  df = pd.DataFrame({
      'clicks': np.random.choice(range(20), n),
      'impressions': np.random.choice(range(20), n),
      'pre_clicks': np.random.choice(range(20), n),
      'cookie': np.random.choice(range(4), n),
      'condition': np.random.choice(range(2), n),
      'grp': np.random.choice(('A', 'B', 'C'), n),
  })
  sum_click = metrics.Sum('clicks')
  sum_preclick = metrics.Sum('pre_clicks')
  df_agg = df.groupby(['cookie', 'condition']).sum().reset_index()
  df_agg.pre_clicks = df_agg.pre_clicks - df_agg.pre_clicks.mean()
  df_agg['interaction'] = df_agg.pre_clicks * df_agg.condition
  x = df_agg[['condition', 'pre_clicks', 'interaction']]
  y = df_agg['clicks']

  def test_basic(self):
    metric = operations.PrePostChange('condition', 0, self.sum_click,
                                      self.sum_preclick, 'cookie')
    output = metric.compute_on(self.df)
    lm = linear_model.LinearRegression()
    lm.fit(self.x, self.y)
    expected = pd.DataFrame([[100 * lm.coef_[0] / lm.intercept_]],
                            columns=['sum(clicks) PrePost Percent Change'],
                            index=[1])
    expected.index.name = 'condition'
    testing.assert_frame_equal(output, expected)

  def test_include_base(self):
    metric = operations.PrePostChange('condition', 0, self.sum_click,
                                      self.sum_preclick, 'cookie', True)
    output = metric.compute_on(self.df)
    lm = linear_model.LinearRegression()
    lm.fit(self.x, self.y)
    expected = pd.DataFrame([0, 100 * lm.coef_[0] / lm.intercept_],
                            columns=['sum(clicks) PrePost Percent Change'],
                            index=[0, 1])
    expected.index.name = 'condition'
    testing.assert_frame_equal(output, expected)

  def test_split_by(self):
    metric = operations.PrePostChange('condition', 0, self.sum_click,
                                      self.sum_preclick, 'cookie')
    output = metric.compute_on(self.df, 'grp')
    expected_a = metric.compute_on(self.df[self.df.grp == 'A'])
    expected_b = metric.compute_on(self.df[self.df.grp == 'B'])
    expected_c = metric.compute_on(self.df[self.df.grp == 'C'])
    expected = pd.concat((expected_a, expected_b, expected_c),
                         keys=('A', 'B', 'C'),
                         names=['grp'])
    testing.assert_frame_equal(output, expected)

  def test_split_by_multiple(self):
    metric = operations.PrePostChange('condition', 0, self.sum_click,
                                      self.sum_preclick, 'cookie')
    output = metric.compute_on(self.df, 'grp')
    expected_a = metric.compute_on(self.df[self.df.grp == 'A'])
    expected_b = metric.compute_on(self.df[self.df.grp == 'B'])
    expected_c = metric.compute_on(self.df[self.df.grp == 'C'])
    expected = pd.concat((expected_a, expected_b, expected_c),
                         keys=('A', 'B', 'C'),
                         names=['grp'])
    testing.assert_frame_equal(output, expected)

  def test_multiple_conditions(self):
    metric = operations.PrePostChange(['condition', 'grp'], (0, 'C'),
                                      self.sum_click, self.sum_preclick,
                                      'cookie')
    output = metric.compute_on(self.df)
    df = self.df.copy()
    df['condition_and_grp'] = df[['condition', 'grp']].apply(tuple, 1)
    expected_metric = operations.PrePostChange('condition_and_grp', (0, 'C'),
                                               self.sum_click,
                                               self.sum_preclick, 'cookie')
    expected = expected_metric.compute_on(df)
    expected = pd.DataFrame(
        expected.values, index=output.index, columns=output.columns)
    testing.assert_frame_equal(output, expected)

  def test_multiple_stratified_by(self):
    metric = operations.PrePostChange('condition', 0, self.sum_click,
                                      self.sum_preclick, ['cookie', 'grp'])
    output = metric.compute_on(self.df)
    df_agg = self.df.groupby(['cookie', 'grp', 'condition']).sum().reset_index()
    df_agg.pre_clicks = df_agg.pre_clicks - df_agg.pre_clicks.mean()
    df_agg['interaction'] = df_agg.pre_clicks * df_agg.condition
    lm = linear_model.LinearRegression()
    lm.fit(df_agg[['condition', 'pre_clicks', 'interaction']], df_agg['clicks'])
    expected = pd.DataFrame([100 * lm.coef_[0] / lm.intercept_],
                            columns=['sum(clicks) PrePost Percent Change'],
                            index=[1])
    expected.index.name = 'condition'
    testing.assert_frame_equal(output, expected)

  def test_multiple_metrics(self):
    metric = operations.PrePostChange(
        'condition', 0,
        metrics.MetricList([self.sum_click,
                            metrics.Sum('impressions')]), self.sum_preclick,
        'cookie')
    output = metric.compute_on(self.df)
    expected1 = operations.PrePostChange('condition', 0, self.sum_click,
                                         self.sum_preclick,
                                         'cookie').compute_on(self.df)
    expected2 = operations.PrePostChange('condition', 0,
                                         metrics.Sum('impressions'),
                                         self.sum_preclick,
                                         'cookie').compute_on(self.df)
    expected = pd.concat((expected1, expected2), 1)
    testing.assert_frame_equal(output, expected)

  def test_multiple_covariates(self):
    metric = operations.PrePostChange(
        'condition', 0, self.sum_click,
        [self.sum_preclick, metrics.Sum('impressions')], 'cookie')
    output = metric.compute_on(self.df)
    df = self.df.copy()
    df_agg = df.groupby(['cookie', 'condition']).sum().reset_index()
    df_agg.pre_clicks = df_agg.pre_clicks - df_agg.pre_clicks.mean()
    df_agg.impressions = df_agg.impressions - df_agg.impressions.mean()
    df_agg['interaction1'] = df_agg.pre_clicks * df_agg.condition
    df_agg['interaction2'] = df_agg.impressions * df_agg.condition
    lm = linear_model.LinearRegression()
    lm.fit(
        df_agg[[
            'condition', 'pre_clicks', 'impressions', 'interaction1',
            'interaction2'
        ]], df_agg['clicks'])
    expected = pd.DataFrame([100 * lm.coef_[0] / lm.intercept_],
                            columns=['sum(clicks) PrePost Percent Change'],
                            index=[1])
    expected.index.name = 'condition'
    testing.assert_frame_equal(output, expected)

  def test_complex(self):
    n = 50
    df = pd.DataFrame({
        'clicks': np.random.choice(range(20), n),
        'impressions': np.random.choice(range(20), n),
        'pre_clicks': np.random.choice(range(20), n),
        'cookie': np.random.choice(range(5), n),
        'condition1': np.random.choice(range(2), n),
        'condition2': np.random.choice(('A', 'B', 'C'), n),
        'grp1': np.random.choice(('foo', 'bar', 'baz'), n),
        'grp2': np.random.choice(('US', 'non-US'), n),
        'grp3': np.random.choice(('desktop', 'mobile', 'tablet'), n),
    })
    post = [self.sum_click, self.sum_click**2]
    pre = [self.sum_preclick, metrics.Sum('impressions')]
    metric = operations.PrePostChange(['condition1', 'condition2'], (1, 'C'),
                                      metrics.MetricList(post), pre,
                                      ['cookie', 'grp1'])
    output = metric.compute_on(df, ['grp2', 'grp3'])
    df['condition'] = df[['condition1', 'condition2']].apply(tuple, 1)
    df['agg'] = df[['cookie', 'grp1']].apply(tuple, 1)

    expected = [
        operations.PrePostChange('condition', (1, 'C'), m, pre,
                                 'agg').compute_on(df, ['grp2', 'grp3'])
        for m in post
    ]
    expected = pd.concat(expected, axis=1)
    expected = expected.reset_index('condition')
    expected[['condition1', 'condition2']] = expected.condition.to_list()
    expected = expected.set_index(['condition1', 'condition2'], append=True)
    expected = expected.drop(columns=['condition'])
    testing.assert_frame_equal(output, expected)

  def test_where(self):
    metric = operations.PrePostChange(
        'condition',
        0,
        self.sum_click,
        self.sum_preclick,
        'cookie',
        where='grp == "A"')
    metric_no_filter = operations.PrePostChange('condition', 0, self.sum_click,
                                                self.sum_preclick, 'cookie')
    output = metric.compute_on(self.df)
    expected = metric_no_filter.compute_on(self.df[self.df.grp == 'A'])
    testing.assert_frame_equal(output, expected)

  def test_with_jackknife(self):
    df = self.df.copy()
    df['grp2'] = np.random.choice(range(3), self.n)
    m = operations.PrePostChange('condition', 0, self.sum_click,
                                 self.sum_preclick, 'grp2')
    jk = operations.Jackknife('cookie', m)
    output = jk.compute_on(df)
    loo = []
    for g in df.cookie.unique():
      loo.append(m.compute_on(df[df.cookie != g]))
    loo = pd.concat(loo)
    dof = len(df.cookie.unique()) - 1
    expected = pd.DataFrame(
        [[
            m.compute_on(df).iloc[0, 0],
            loo.std().values[0] * dof / np.sqrt(dof + 1)
        ]],
        index=[1],
        columns=pd.MultiIndex.from_product(
            [['sum(clicks) PrePost Percent Change'], ['Value', 'Jackknife SE']],
            names=['Metric', None]))
    expected.index.name = 'condition'
    testing.assert_frame_equal(output, expected)

  def test_with_jackknife_with_overlapping_column(self):
    df = self.df.copy()
    m = operations.PrePostChange('condition', 0, self.sum_click,
                                 self.sum_preclick, 'cookie')
    jk = operations.Jackknife('cookie', m)
    output = jk.compute_on(df)
    loo = []
    for g in df.cookie.unique():
      loo.append(m.compute_on(df[df.cookie != g]))
    loo = pd.concat(loo)
    dof = len(df.cookie.unique()) - 1
    expected = pd.DataFrame(
        [[
            m.compute_on(df).iloc[0, 0],
            loo.std().values[0] * dof / np.sqrt(dof + 1)
        ]],
        index=[1],
        columns=pd.MultiIndex.from_product(
            [['sum(clicks) PrePost Percent Change'], ['Value', 'Jackknife SE']],
            names=['Metric', None]))
    expected.index.name = 'condition'
    testing.assert_frame_equal(output, expected)

  def test_display(self):
    df = self.df.copy()
    jk = operations.Jackknife('cookie', confidence=0.9)
    prepost = operations.PrePostChange('condition', 0, self.sum_click,
                                       self.sum_preclick, 'cookie')
    pct = operations.PercentChange('condition', 0, self.sum_click)
    output = jk(prepost).compute_on(df).display(return_formatted_df=True)
    expected = jk(pct).compute_on(df).display(return_formatted_df=True)
    self.assertEqual(output.shape, expected.shape)


class CUPEDTests(unittest.TestCase):

  n = 40
  df = pd.DataFrame({
      'clicks': np.random.choice(range(20), n),
      'impressions': np.random.choice(range(20), n),
      'pre_clicks': np.random.choice(range(20), n),
      'cookie': np.random.choice(range(4), n),
      'condition': np.random.choice(range(2), n),
      'grp': np.random.choice(('A', 'B', 'C'), n),
  })
  sum_click = metrics.Sum('clicks')
  sum_preclick = metrics.Sum('pre_clicks')
  df_agg = df.groupby(['cookie', 'condition']).sum().reset_index()
  df_agg.pre_clicks = df_agg.pre_clicks - df_agg.pre_clicks.mean()
  df_agg.impressions = df_agg.impressions - df_agg.impressions.mean()

  def test_basic(self):
    metric = operations.CUPED('condition', 0, self.sum_click, self.sum_preclick,
                              'cookie')
    output = metric.compute_on(self.df)
    theta = self.df_agg[['clicks', 'pre_clicks'
                        ]].cov().iloc[0, 1] / self.df_agg.pre_clicks.var()
    adjusted = self.df_agg.groupby('condition').clicks.mean(
    ) - theta * self.df_agg.groupby('condition').pre_clicks.mean()
    expected = pd.DataFrame([[adjusted[1] - adjusted[0]]],
                            columns=['sum(clicks) CUPED Change'],
                            index=[1])
    expected.index.name = 'condition'
    testing.assert_frame_equal(output, expected)

  def test_include_base(self):
    metric = operations.CUPED('condition', 0, self.sum_click, self.sum_preclick,
                              'cookie', True)
    output = metric.compute_on(self.df)
    theta = self.df_agg[['clicks', 'pre_clicks'
                        ]].cov().iloc[0, 1] / self.df_agg.pre_clicks.var()
    adjusted = self.df_agg.groupby('condition').clicks.mean(
    ) - theta * self.df_agg.groupby('condition').pre_clicks.mean()
    expected = pd.DataFrame([0, adjusted[1] - adjusted[0]],
                            columns=['sum(clicks) CUPED Change'],
                            index=[0, 1])
    expected.index.name = 'condition'
    testing.assert_frame_equal(output, expected)

  def test_split_by(self):
    metric = operations.CUPED('condition', 0, self.sum_click, self.sum_preclick,
                              'cookie')
    output = metric.compute_on(self.df, 'grp')
    expected_a = metric.compute_on(self.df[self.df.grp == 'A'])
    expected_b = metric.compute_on(self.df[self.df.grp == 'B'])
    expected_c = metric.compute_on(self.df[self.df.grp == 'C'])
    expected = pd.concat((expected_a, expected_b, expected_c),
                         keys=('A', 'B', 'C'),
                         names=['grp'])
    testing.assert_frame_equal(output, expected)

  def test_split_by_multiple(self):
    metric = operations.CUPED('condition', 0, self.sum_click, self.sum_preclick,
                              'cookie')
    output = metric.compute_on(self.df, 'grp')
    expected_a = metric.compute_on(self.df[self.df.grp == 'A'])
    expected_b = metric.compute_on(self.df[self.df.grp == 'B'])
    expected_c = metric.compute_on(self.df[self.df.grp == 'C'])
    expected = pd.concat((expected_a, expected_b, expected_c),
                         keys=('A', 'B', 'C'),
                         names=['grp'])
    testing.assert_frame_equal(output, expected)

  def test_multiple_conditions(self):
    metric = operations.CUPED(['condition', 'grp'], (0, 'C'), self.sum_click,
                              self.sum_preclick, 'cookie')
    output = metric.compute_on(self.df)
    df = self.df.copy()
    df['condition_and_grp'] = df[['condition', 'grp']].apply(tuple, 1)
    expected_metric = operations.CUPED('condition_and_grp', (0, 'C'),
                                       self.sum_click, self.sum_preclick,
                                       'cookie')
    expected = expected_metric.compute_on(df)
    expected = pd.DataFrame(
        expected.values, index=output.index, columns=output.columns)
    testing.assert_frame_equal(output, expected)

  def test_multiple_stratified_by(self):
    metric = operations.CUPED('condition', 0, self.sum_click, self.sum_preclick,
                              ['cookie', 'grp'])
    output = metric.compute_on(self.df)
    df_agg = self.df.groupby(['cookie', 'condition', 'grp']).sum().reset_index()
    df_agg.pre_clicks = df_agg.pre_clicks - df_agg.pre_clicks.mean()
    df_agg.impressions = df_agg.impressions - df_agg.impressions.mean()
    theta = df_agg[['clicks', 'pre_clicks'
                   ]].cov().iloc[0, 1] / df_agg.pre_clicks.var()
    adjusted = df_agg.groupby('condition').clicks.mean(
    ) - theta * df_agg.groupby('condition').pre_clicks.mean()
    expected = pd.DataFrame([[adjusted[1] - adjusted[0]]],
                            columns=['sum(clicks) CUPED Change'],
                            index=[1])
    expected.index.name = 'condition'
    testing.assert_frame_equal(output, expected)

  def test_multiple_metrics(self):
    metric = operations.CUPED(
        'condition', 0,
        metrics.MetricList([self.sum_click,
                            metrics.Sum('impressions')]), self.sum_preclick,
        'cookie')
    output = metric.compute_on(self.df)
    expected1 = operations.CUPED('condition', 0, self.sum_click,
                                 self.sum_preclick,
                                 'cookie').compute_on(self.df)
    expected2 = operations.CUPED('condition', 0, metrics.Sum('impressions'),
                                 self.sum_preclick,
                                 'cookie').compute_on(self.df)
    expected = pd.concat((expected1, expected2), 1)
    testing.assert_frame_equal(output, expected)

  def test_multiple_covariates(self):
    metric = operations.CUPED(
        'condition', 0, self.sum_click,
        [self.sum_preclick, metrics.Sum('impressions')], 'cookie')
    output = metric.compute_on(self.df)
    lm = linear_model.LinearRegression()
    lm.fit(self.df_agg[['pre_clicks', 'impressions']], self.df_agg['clicks'])
    theta = lm.coef_
    adjusted = self.df_agg.groupby('condition').clicks.mean(
    ) - theta[0] * self.df_agg.groupby('condition').pre_clicks.mean(
    ) - theta[1] * self.df_agg.groupby('condition').impressions.mean()
    expected = pd.DataFrame(
        adjusted[1] - adjusted[0],
        columns=['sum(clicks) CUPED Change'],
        index=[1])
    expected.index.name = 'condition'
    testing.assert_frame_equal(output, expected)

  def test_complex(self):
    n = 50
    df = pd.DataFrame({
        'clicks': np.random.choice(range(20), n),
        'impressions': np.random.choice(range(20), n),
        'pre_clicks': np.random.choice(range(20), n),
        'cookie': np.random.choice(range(5), n),
        'condition1': np.random.choice(range(2), n),
        'condition2': np.random.choice(('A', 'B', 'C'), n),
        'grp1': np.random.choice(('foo', 'bar', 'baz'), n),
        'grp2': np.random.choice(('US', 'non-US'), n),
        'grp3': np.random.choice(('desktop', 'mobile', 'tablet'), n),
    })
    post = [self.sum_click, self.sum_click**2]
    pre = [self.sum_preclick, metrics.Sum('impressions')]
    metric = operations.CUPED(['condition1', 'condition2'], (1, 'C'),
                              metrics.MetricList(post), pre, ['cookie', 'grp1'])
    output = metric.compute_on(df, ['grp2', 'grp3'])
    df['condition'] = df[['condition1', 'condition2']].apply(tuple, 1)
    df['agg'] = df[['cookie', 'grp1']].apply(tuple, 1)

    expected = [
        operations.CUPED('condition', (1, 'C'), m, pre,
                         'agg').compute_on(df, ['grp2', 'grp3']) for m in post
    ]
    expected = pd.concat(expected, axis=1)
    expected = expected.reset_index('condition')
    expected[['condition1', 'condition2']] = expected.condition.to_list()
    expected = expected.set_index(['condition1', 'condition2'], append=True)
    expected = expected.drop(columns=['condition'])
    testing.assert_frame_equal(output, expected)

  def test_where(self):
    metric = operations.CUPED(
        'condition',
        0,
        self.sum_click,
        self.sum_preclick,
        'cookie',
        where='grp == "A"')
    metric_no_filter = operations.CUPED('condition', 0, self.sum_click,
                                        self.sum_preclick, 'cookie')
    output = metric.compute_on(self.df)
    expected = metric_no_filter.compute_on(self.df[self.df.grp == 'A'])
    testing.assert_frame_equal(output, expected)

  def test_with_jackknife(self):
    df = self.df.copy()
    df['grp2'] = np.random.choice(range(3), self.n)
    m = operations.CUPED('condition', 0, self.sum_click, self.sum_preclick,
                         'grp2')
    jk = operations.Jackknife('cookie', m)
    output = jk.compute_on(df)
    loo = []
    for g in df.cookie.unique():
      loo.append(m.compute_on(df[df.cookie != g]))
    loo = pd.concat(loo)
    dof = len(df.cookie.unique()) - 1
    expected = pd.DataFrame(
        [[
            m.compute_on(df).iloc[0, 0],
            loo.std().values[0] * dof / np.sqrt(dof + 1)
        ]],
        index=[1],
        columns=pd.MultiIndex.from_product(
            [['sum(clicks) CUPED Change'], ['Value', 'Jackknife SE']],
            names=['Metric', None]))
    expected.index.name = 'condition'
    testing.assert_frame_equal(output, expected)

  def test_with_jackknife_with_overlapping_column(self):
    df = self.df.copy()
    m = operations.CUPED('condition', 0, self.sum_click, self.sum_preclick,
                         'cookie')
    jk = operations.Jackknife('cookie', m)
    output = jk.compute_on(df)
    loo = []
    for g in df.cookie.unique():
      loo.append(m.compute_on(df[df.cookie != g]))
    loo = pd.concat(loo)
    dof = len(df.cookie.unique()) - 1
    expected = pd.DataFrame(
        [[
            m.compute_on(df).iloc[0, 0],
            loo.std().values[0] * dof / np.sqrt(dof + 1)
        ]],
        index=[1],
        columns=pd.MultiIndex.from_product(
            [['sum(clicks) CUPED Change'], ['Value', 'Jackknife SE']],
            names=['Metric', None]))
    expected.index.name = 'condition'
    testing.assert_frame_equal(output, expected)

  def test_display(self):
    df = self.df.copy()
    jk = operations.Jackknife('cookie', confidence=0.9)
    prepost = operations.CUPED('condition', 0, self.sum_click,
                               self.sum_preclick, 'cookie')
    pct = operations.PercentChange('condition', 0, self.sum_click)
    output = jk(prepost).compute_on(df).display(return_formatted_df=True)
    expected = jk(pct).compute_on(df).display(return_formatted_df=True)
    self.assertEqual(output.shape, expected.shape)


class MHTests(unittest.TestCase):

  df = pd.DataFrame({
      'clicks': [1, 3, 2, 3, 1, 2],
      'conversions': [1, 0, 1, 2, 1, 1],
      'Id': [1, 2, 3, 1, 2, 3],
      'Condition': [0, 0, 0, 1, 1, 1]
  })
  sum_click = metrics.Sum('clicks')
  sum_conv = metrics.Sum('conversions')
  cvr = metrics.Ratio('conversions', 'clicks', 'cvr')
  metric_lst = metrics.MetricList((sum_conv / sum_click, cvr))

  def test_mh(self):
    metric = operations.MH('Condition', 0, 'Id', self.cvr)
    output = metric.compute_on(self.df)
    expected = pd.DataFrame([[40.]], columns=['cvr MH Ratio'], index=[1])
    expected.index.name = 'Condition'
    testing.assert_frame_equal(output, expected)

  def test_mh_include_baseline(self):
    metric = operations.MH('Condition', 0, 'Id', self.metric_lst, True)
    output = metric.compute_on(self.df)
    expected = pd.DataFrame(
        [[0., 0.], [40., 40.]],
        columns=['sum(conversions) / sum(clicks) MH Ratio', 'cvr MH Ratio'],
        index=[0, 1])
    expected.index.name = 'Condition'
    testing.assert_frame_equal(output, expected)

  def test_mh_melted(self):
    metric = operations.MH('Condition', 0, 'Id', self.metric_lst)
    output = metric.compute_on(self.df, melted=True)
    expected = pd.DataFrame({
        'Value': [40., 40.],
        'Metric': ['sum(conversions) / sum(clicks) MH Ratio', 'cvr MH Ratio'],
        'Condition': [1, 1]
    })
    expected.set_index(['Metric', 'Condition'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_mh_melted_include_baseline(self):
    metric = operations.MH('Condition', 0, 'Id', self.metric_lst, True)
    output = metric.compute_on(self.df, melted=True)
    expected = expected = pd.DataFrame({
        'Value': [0., 40., 0., 40.],
        'Metric': [
            'sum(conversions) / sum(clicks) MH Ratio',
            'sum(conversions) / sum(clicks) MH Ratio', 'cvr MH Ratio',
            'cvr MH Ratio'
        ],
        'Condition': [0, 1, 0, 1]
    })
    expected.set_index(['Metric', 'Condition'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_mh_splitby(self):
    df = pd.DataFrame({
        'clicks': [1, 3, 2, 3, 1, 2] * 2,
        'conversions': [1, 0, 1, 2, 1, 1, 1, 0, 1, 2, 1, 2],
        'Id': [1, 2, 3, 1, 2, 3] * 2,
        'Condition': [0, 0, 0, 1, 1, 1] * 2,
        'grp': ['A'] * 6 + ['B'] * 6
    })
    metric = operations.MH('Condition', 0, 'Id', self.metric_lst)
    output = metric.compute_on(df, 'grp')
    output.sort_index(level='grp', inplace=True)  # For Py2
    expected = pd.DataFrame([['A', 1, 40., 40.], ['B', 1, 80., 80.]],
                            columns=[
                                'grp', 'Condition',
                                'sum(conversions) / sum(clicks) MH Ratio',
                                'cvr MH Ratio'
                            ])
    expected.set_index(['grp', 'Condition'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_mh_splitby_melted(self):
    df = pd.DataFrame({
        'clicks': [1, 3, 2, 3, 1, 2] * 2,
        'conversions': [1, 0, 1, 2, 1, 1, 1, 0, 1, 2, 1, 2],
        'Id': [1, 2, 3, 1, 2, 3] * 2,
        'Condition': [0, 0, 0, 1, 1, 1] * 2,
        'grp': ['A'] * 6 + ['B'] * 6
    })
    metric = operations.MH('Condition', 0, 'Id', self.metric_lst, True)
    output = metric.compute_on(df, 'grp', melted=True)
    output.sort_index(
        level=['Metric', 'grp'], ascending=[False, True],
        inplace=True)  # For Py2
    expected = pd.DataFrame({
        'Value': [0., 40., 0., 80., 0., 40., 0., 80.],
        'Metric': ['sum(conversions) / sum(clicks) MH Ratio'] * 4 +
                  ['cvr MH Ratio'] * 4,
        'Condition': [0, 1] * 4,
        'grp': ['A', 'A', 'B', 'B'] * 2
    })
    expected.set_index(['Metric', 'grp', 'Condition'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_mh_multiple_condition_columns(self):
    df = pd.DataFrame({
        'clicks': [1, 3, 2, 3, 1, 2] * 2,
        'conversions': [1, 0, 1, 2, 1, 1] * 2,
        'Id': [1, 2, 3, 1, 2, 3] * 2,
        'Condition': [0, 0, 0, 1, 1, 1] * 2,
        'grp': ['A', 'B'] * 6,
    })
    metric = operations.MH(['Condition', 'grp'], (0, 'A'), 'Id',
                           self.metric_lst)
    output = metric.compute_on(df)
    df['Condition_and_grp'] = df[['Condition', 'grp']].apply(tuple, 1)
    expected_metric = operations.MH('Condition_and_grp', (0, 'A'), 'Id',
                                    self.metric_lst)
    expected = expected_metric.compute_on(df)
    expected = pd.DataFrame(
        expected.values, index=output.index, columns=output.columns)
    testing.assert_frame_equal(output, expected)

  def test_mh_multiple_condition_columns_include_baseline(self):
    df = pd.DataFrame({
        'clicks': [1, 3, 2, 3, 1, 2] * 2,
        'conversions': [1, 0, 1, 2, 1, 1] * 2,
        'Id': [1, 2, 3, 1, 2, 3] * 2,
        'Condition': [0, 0, 0, 1, 1, 1] * 2,
        'grp': ['A', 'B'] * 6,
    })
    metric = operations.MH(['Condition', 'grp'], (0, 'A'), 'Id',
                           self.metric_lst, True)
    output = metric.compute_on(df)
    df['Condition_and_grp'] = df[['Condition', 'grp']].apply(tuple, 1)
    expected_metric = operations.MH('Condition_and_grp', (0, 'A'), 'Id',
                                    self.metric_lst, True)
    expected = expected_metric.compute_on(df)
    expected = pd.DataFrame(
        expected.values, index=output.index, columns=output.columns)
    testing.assert_frame_equal(output, expected)

  def test_mh_multiple_condition_columns_splitby(self):
    df = pd.DataFrame({
        'clicks': [1, 3, 2, 3, 1, 2] * 2,
        'conversions': [1, 0, 1, 2, 1, 1] * 2,
        'Id': [1, 2, 3, 1, 2, 3] * 2,
        'Condition': [0, 0, 0, 1, 1, 1] * 2,
        'grp': ['A', 'B'] * 6,
        'grp2': ['foo', 'foo', 'bar'] * 4,
    })
    sum_click = metrics.Sum('clicks')
    sum_conv = metrics.Sum('conversions')
    self.metric_lst = metrics.MetricList(
        (sum_conv / sum_click, metrics.Ratio('conversions', 'clicks', 'cvr')))
    metric = operations.MH(['Condition', 'grp'], (0, 'A'), 'Id',
                           self.metric_lst)
    output = metric.compute_on(df, 'grp2')
    df['Condition_and_grp'] = df[['Condition', 'grp']].apply(tuple, 1)
    expected_metric = operations.MH('Condition_and_grp', (0, 'A'), 'Id',
                                    self.metric_lst)
    expected = expected_metric.compute_on(df, 'grp2')
    expected = pd.DataFrame(
        expected.values, index=output.index, columns=output.columns)
    testing.assert_frame_equal(output, expected)

  def test_mh_splitby_multiple(self):
    df = pd.DataFrame({
        'clicks': np.random.random(24),
        'conversions': np.random.random(24),
        'Id': [1, 2, 3, 1, 2, 3] * 4,
        'Condition': [0, 0, 0, 1, 1, 1] * 4,
        'grp': (['A'] * 6 + ['B'] * 6) * 2,
        'grp0': ['foo'] * 12 + ['bar'] * 12
    })
    metric = operations.MH('Condition', 0, 'Id', self.metric_lst)
    output = metric.compute_on(df, ['grp0', 'grp'])
    output.sort_index(level=['grp0', 'grp'], inplace=True)  # For Py2
    bar = metric.compute_on(df[df.grp0 == 'bar'], 'grp')
    foo = metric.compute_on(df[df.grp0 == 'foo'], 'grp')
    expected = pd.concat([bar, foo], keys=['bar', 'foo'], names=['grp0'])
    expected.sort_index(level=['grp0', 'grp'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_mh_stratified_by_multiple(self):
    df = pd.DataFrame({
        'clicks': [1, 3, 2, 3, 1, 2, 12, 31, 22, 30, 15, 23],
        'conversions': [1, 0, 1, 2, 1, 1, 3, 2, 4, 6, 7, 1],
        'Id': [1, 2, 3, 1, 2, 3] * 2,
        'platform': ['Desktop'] * 6 + ['Mobile'] * 6,
        'Condition': [0, 0, 0, 1, 1, 1] * 2
    })
    df['id_platform'] = df[['Id', 'platform']].apply(tuple, axis=1)
    cvr = metrics.Ratio('conversions', 'clicks', 'cvr')

    metric = operations.MH('Condition', 0, ['Id', 'platform'], cvr)
    output = metric.compute_on(df)
    expected = operations.MH('Condition', 0, 'id_platform', cvr).compute_on(df)
    testing.assert_frame_equal(output, expected)

  def test_mh_on_operations(self):
    df = pd.DataFrame({
        'clicks': np.random.random(24),
        'conversions': np.random.random(24),
        'Id': [1, 2, 1, 2] * 6,
        'Condition': [0, 0, 0, 1, 1, 1] * 4,
        'grp': list('AABBCCBC') * 3,
    })
    sum_clicks = metrics.Sum('clicks')
    ab = operations.AbsoluteChange('grp', 'A', sum_clicks)
    pct = operations.PercentChange('grp', 'A', sum_clicks)
    metric = operations.MH('Condition', 0, 'Id', ab / pct)
    output = metric.compute_on(df)
    d = (metrics.MetricList(
        (ab, pct))).compute_on(df, ['Condition', 'Id']).reset_index()
    m = metric(metrics.Sum(ab.name) / metrics.Sum(pct.name))
    expected = m.compute_on(d, 'grp')
    expected.columns = output.columns
    expected = expected.reorder_levels(output.index.names)
    testing.assert_frame_equal(output, expected)

  def test_mh_fail_on_nonratio_metric(self):
    with self.assertRaisesRegex(ValueError,
                                'MH only makes sense on ratio Metrics.'):
      operations.MH('Condition', 0, 'Id', self.sum_click).compute_on(self.df)

  def test_mh_pipeline(self):
    metric = operations.MH('Condition', 0, 'Id')
    output = self.metric_lst | metric | metrics.compute_on(self.df)
    expected = pd.DataFrame(
        [[40., 40.]],
        columns=['sum(conversions) / sum(clicks) MH Ratio', 'cvr MH Ratio'],
        index=[1])
    expected.index.name = 'Condition'
    testing.assert_frame_equal(output, expected)

  def test_internal_caching(self):
    sum_click = metrics.Sum('clicks')
    sum_conv = metrics.Sum('conversions')
    m = metrics.MetricList((sum_conv / sum_click, sum_conv / sum_conv))
    with mock.patch.object(
        sum_conv, 'compute_through', return_value=1, autospec=True) as mock_fn:
      m.compute_on(self.df, return_dataframe=False)
      mock_fn.assert_called_once()

  def test_cache_key(self):
    sum_click = metrics.Sum('clicks')
    sum_conv = metrics.Sum('conversions')
    metric_lst = metrics.MetricList([sum_conv / sum_click])
    metric = operations.MH('Condition', 0, 'Id', metric_lst)
    metric.compute_on(self.df, cache_key=42)
    testing.assert_series_equal(
        self.df.groupby(['Condition', 'Id']).clicks.sum(),
        sum_click.get_cached(42, ['Condition', 'Id']))
    testing.assert_series_equal(
        self.df.groupby(['Condition', 'Id']).conversions.sum(),
        sum_conv.get_cached(42, ['Condition', 'Id']))
    self.assertTrue(metric.in_cache(42))

  def test_internal_caching_cleaned_up(self):
    sum_click = metrics.Sum('clicks')
    sum_conv = metrics.Sum('conversions')
    metric_lst = metrics.MetricList([sum_conv / sum_click])
    m = operations.MH('Condition', 0, 'Id', metric_lst)
    m.compute_on(self.df)
    self.assertEqual(m.cache, {})
    self.assertEqual(metric_lst.cache, {})
    self.assertEqual(sum_click.cache, {})
    self.assertEqual(sum_conv.cache, {})

    self.assertIsNone(m.cache_key)
    self.assertIsNone(metric_lst.cache_key)
    self.assertIsNone(sum_click.cache_key)
    self.assertIsNone(sum_conv.cache_key)

  def test_with_jackknife_internal_caching_cleaned_up(self):
    df = pd.DataFrame({
        'clicks': [1, 3, 2, 3, 1, 2],
        'conversions': [1, 0, 1, 2, 1, 1],
        'Id': [1, 2, 3, 1, 2, 3],
        'Condition': [0, 0, 0, 1, 1, 1],
        'cookie': [1, 2, 3] * 2
    })
    sum_click = metrics.Sum('clicks')
    sum_conv = metrics.Sum('conversions')
    metric_lst = metrics.MetricList([sum_conv / sum_click])
    m = operations.MH('Condition', 0, 'Id', metric_lst)
    jk = operations.Jackknife('cookie', m)
    jk.compute_on(df)

    self.assertEqual(jk.cache, {})
    self.assertEqual(m.cache, {})
    self.assertEqual(metric_lst.cache, {})
    self.assertEqual(sum_click.cache, {})
    self.assertEqual(sum_conv.cache, {})

    self.assertIsNone(jk.cache_key)
    self.assertIsNone(m.cache_key)
    self.assertIsNone(metric_lst.cache_key)
    self.assertIsNone(sum_click.cache_key)
    self.assertIsNone(sum_conv.cache_key)


class JackknifeTests(unittest.TestCase):

  count_x0 = metrics.Sum('X') / metrics.Mean('X')
  count_x1 = metrics.Count('X')
  count_x2 = metrics.Metric('count_ground_truth', compute=lambda x: x.X.count())
  dot1 = metrics.Dot('X', 'X')
  dot2 = metrics.Dot('X', 'X', True)
  metric = metrics.MetricList((count_x0, count_x1, count_x2, dot1, dot2))
  change = operations.AbsoluteChange('condition', 'foo', metric)
  jk = operations.Jackknife('cookie', metric)
  jk_change = operations.Jackknife('cookie', change)

  def test_jackknife(self):
    df = pd.DataFrame({'X': np.arange(0, 3, 0.5), 'cookie': [1, 2, 2, 1, 2, 2]})
    unmelted = self.jk.compute_on(df)
    expected = pd.DataFrame(
        [[6., 1.] * 3 + [(df.X**2).sum(), 4.625, (df.X**2).mean(), 0.875]],
        columns=pd.MultiIndex.from_product([[
            'sum(X) / mean(X)', 'count(X)', 'count_ground_truth', 'sum(X * X)',
            'mean(X * X)'
        ], ['Value', 'Jackknife SE']],
                                           names=['Metric', None]))
    testing.assert_frame_equal(unmelted, expected)

    melted = self.jk.compute_on(df, melted=True)
    expected = pd.DataFrame(
        data={
            'Value': [6.] * 3 + [(df.X**2).sum(), (df.X**2).mean()],
            'Jackknife SE': [1, 1, 1, 4.625, 0.875]
        },
        columns=['Value', 'Jackknife SE'],
        index=[
            'sum(X) / mean(X)', 'count(X)', 'count_ground_truth', 'sum(X * X)',
            'mean(X * X)'
        ])
    expected.index.name = 'Metric'
    testing.assert_frame_equal(melted, expected)

  def test_jackknife_with_weighted_mean(self):
    df = pd.DataFrame({'X': [1, 2, 2], 'W': [1, 2, 2], 'cookie': [1, 2, 2]})
    mean = metrics.Mean('X', 'W')
    jk = operations.Jackknife('cookie', mean)
    output = jk.compute_on(df)
    expected = pd.DataFrame(
        [[1.8, 0.5]],
        columns=pd.MultiIndex.from_product(
            [['W-weighted mean(X)'], ['Value', 'Jackknife SE']],
            names=['Metric', None]))
    testing.assert_frame_equal(output, expected)

  def test_jackknife_too_few_buckets(self):
    df = pd.DataFrame({'X': range(2), 'cookie': [1, 1]})
    with self.assertRaises(ValueError) as cm:
      self.jk.compute_on(df)
    self.assertEqual(str(cm.exception), 'Too few cookie to jackknife.')

  def test_jackknife_one_metric_fail_on_one_unit(self):
    df = pd.DataFrame({
        'X': range(1, 7),
        'cookie': [1, 2, 2, 1, 2, 3],
        'grp': ['B'] * 3 + ['A'] * 3
    })
    sum1 = metrics.Sum('X', where='X > 2')
    sum2 = metrics.Sum('X', 'foo', where='X > 4')
    ms = metrics.MetricList((sum1, sum2))
    m = operations.Jackknife('cookie', ms)
    output = m.compute_on(df)
    expected = pd.concat((m(sum1).compute_on(df), m(sum2).compute_on(df)), 1)
    testing.assert_frame_equal(output, expected)

  def test_jackknife_splitby_partial_overlap(self):
    df = pd.DataFrame({
        'X': range(1, 7),
        'cookie': [1, 2, 2, 1, 2, 3],
        'grp': ['B'] * 3 + ['A'] * 3
    })
    unmelted = self.jk.compute_on(df, 'grp')
    expected = []
    for g in ['A', 'B']:
      expected.append(self.jk.compute_on(df[df.grp == g]))
    expected = pd.concat(expected, keys=['A', 'B'], names=['grp'])
    expected = expected.droplevel(-1)
    testing.assert_frame_equal(unmelted, expected)

    melted = self.jk.compute_on(df, 'grp', melted=True)
    testing.assert_frame_equal(melted, utils.melt(expected))

  def test_jackknife_unequal_slice_length_with_confidence(self):
    df = pd.DataFrame({
        'X': range(1, 7),
        'cookie': [1, 2, 2, 1, 2, 3],
        'grp': ['B'] * 3 + ['A'] * 3
    })
    jk = operations.Jackknife('cookie', self.metric, 0.9)
    output = jk.compute_on(df, 'grp')
    expected = []
    for g in ['A', 'B']:
      expected.append(jk.compute_on(df[df.grp == g]))
    expected = pd.concat(expected, keys=['A', 'B'], names=['grp'])
    expected = expected.droplevel(-1)
    testing.assert_frame_equal(output, expected)

  def test_jackknife_splitby_partial_no_overlap(self):
    df = pd.DataFrame({
        'X': range(1, 7),
        'cookie': [1, 2, 2, 3, 3, 4],  # No slice has full levels.
        'grp': ['B'] * 3 + ['A'] * 3
    })
    unmelted = self.jk.compute_on(df, 'grp')
    expected = []
    for g in ['A', 'B']:
      expected.append(self.jk.compute_on(df[df.grp == g]))
    expected = pd.concat(expected, keys=['A', 'B'], names=['grp'])
    expected = expected.droplevel(-1)
    testing.assert_frame_equal(unmelted, expected, check_dtype=False)

    melted = self.jk.compute_on(df, 'grp', melted=True)
    testing.assert_frame_equal(melted, utils.melt(expected), check_dtype=False)

  def test_jackknife_splitby_multiple(self):
    df = pd.DataFrame({
        'X': np.arange(0, 6, 0.5),
        'cookie': [1, 2, 2, 3, 3, 4, 1, 2, 2, 1, 2, 3],
        'grp': list('BBBAAA') * 2,
        'region': ['US'] * 6 + ['non-US'] * 6
    })
    unmelted = self.jk.compute_on(df, ['grp', 'region'])
    expected = []
    for g in ['A', 'B']:
      expected.append(self.jk.compute_on(df[df.grp == g], 'region'))
    expected = pd.concat(expected, keys=['A', 'B'], names=['grp'])
    testing.assert_frame_equal(unmelted, expected)

    melted = self.jk.compute_on(df, ['grp', 'region'], melted=True)
    testing.assert_frame_equal(melted, utils.melt(expected))

  def test_confidence(self):
    df = pd.DataFrame({'X': np.arange(0, 3, 0.5), 'cookie': [1, 2, 3, 1, 2, 3]})
    melted = operations.Jackknife('cookie', self.metric, 0.9).compute_on(
        df, melted=True)
    expected = self.jk.compute_on(df, melted=True)
    multiplier = stats.t.ppf((1 + .9) / 2, 2)
    expected['Jackknife CI-lower'] = expected[
        'Value'] - multiplier * expected['Jackknife SE']
    expected['Jackknife CI-upper'] = expected[
        'Value'] + multiplier * expected['Jackknife SE']
    expected.drop('Jackknife SE', 1, inplace=True)
    testing.assert_frame_equal(melted, expected)
    melted.display()  # Check display() runs.

    unmelted = operations.Jackknife('cookie', self.metric, 0.9).compute_on(df)
    testing.assert_frame_equal(unmelted, utils.unmelt(expected))
    unmelted.display()  # Check display() runs.

  def test_jackknife_one_dof(self):
    df = pd.DataFrame({
        'X': range(2),
        'cookie': [0, 0],
    })
    jk = operations.Jackknife('cookie', metrics.Sum('X'))
    output = jk.compute_on(df)
    expected = pd.DataFrame([[1., np.nan]],
                            columns=pd.MultiIndex.from_product(
                                [['sum(X)'], ['Value', 'Jackknife SE']],
                                names=['Metric', None]))
    testing.assert_frame_equal(output, expected)

  def test_jackknife_where(self):
    df = pd.DataFrame({
        'x': range(7),
        'cookie': [1, 2, 3, 1, 2, 3, 4],
        'condition': [10, 10, 70, 70, 70, 10, 70]
    })
    long_x = metrics.Sum('x', 'long', where='condition > 60')
    long_x_filtered = metrics.MetricList([long_x], where='cookie != 4')
    short_x = metrics.Sum('x', 'short', where='condition < 30')
    std = np.std((1, 1, 7), ddof=1) * 2 / np.sqrt(3)
    m = (long_x_filtered / short_x).rename_columns(['foo'])
    jk = operations.Jackknife('cookie', m)
    output = jk.compute_on(df, melted=True)
    expected = pd.DataFrame([[1.5, std]],
                            columns=['Value', 'Jackknife SE'],
                            index=['foo'])
    expected.index.name = 'Metric'
    testing.assert_frame_equal(output, expected)

  def test_filter_passed_down(self):
    df = pd.DataFrame({'x': range(0, 6), 'u': [1, 2, 3, 1, 2, 3]})
    jk = operations.Jackknife('u', metrics.Sum('x', where='x>2'), where='x>3')
    s = metrics.MetricList([metrics.Sum('x', where='x>3')], where='x>5')
    m = metrics.MetricList([jk, s])
    output = m.compute_on(df, return_dataframe=False)
    expected = [jk.compute_on(df[df.x > 3]), s.compute_on(df[df.x > 5])]
    testing.assert_frame_equal(output[0], expected[0])
    testing.assert_frame_equal(output[1], expected[1])

  def test_cache_key(self):
    df = pd.DataFrame({
        'X': range(6),
        'cookie': [1, 2, 3, 1, 2, 3],
    })
    sum_x = metrics.Sum('X')
    count_x = sum_x / metrics.Mean('X')
    metric = metrics.MetricList((sum_x, count_x))
    jk = operations.Jackknife('cookie', metric)
    jk.compute_on(df, cache_key='foo')
    self.assertEqual(sum(df.X), sum_x.get_cached('foo'))
    self.assertEqual(6, count_x.get_cached('foo'))
    self.assertTrue(jk.in_cache('foo'))

  def test_disable_optimization(self):
    m = metrics.Sum('X')
    jk = operations.Jackknife('cookie', m, enable_optimization=False)
    df = pd.DataFrame({
        'X': range(6),
        'cookie': [1, 2, 3, 1, 2, 3],
    })
    # Don't use autospec=True. It conflicts with wraps.
    # https://bugs.python.org/issue31807
    with mock.patch.object(
        m, 'compute_through', wraps=m.compute_through) as mock_fn:
      jk.compute_on(df)
      self.assertEqual(4, mock_fn.call_count)

  def test_precompute_sum(self):
    m = metrics.Sum('X')
    jk = operations.Jackknife('cookie', m)
    df = pd.DataFrame({
        'X': range(6),
        'cookie': [1, 2, 3, 1, 2, 3],
    })
    # Don't use autospec=True. It conflicts with wraps.
    # https://bugs.python.org/issue31807
    with mock.patch.object(
        m, 'compute_through', wraps=m.compute_through) as mock_fn:
      jk.compute_on(df)
      self.assertEqual(1, mock_fn.call_count)
      mock_fn.assert_has_calls([mock.call(df, [])])

  def test_precompute_mean(self):
    m = metrics.Mean('X')
    jk = operations.Jackknife('cookie', m)
    df = pd.DataFrame({
        'X': range(6),
        'cookie': [1, 2, 3, 1, 2, 3],
    })
    # Don't use autospec=True. It conflicts with wraps.
    # https://bugs.python.org/issue31807
    with mock.patch.object(
        m, 'compute_through', wraps=m.compute_through) as mock_fn:
      jk.compute_on(df)
      self.assertEqual(1, mock_fn.call_count)
      mock_fn.assert_has_calls([mock.call(df, [])])

  def test_precompute_weighted_mean(self):
    m = metrics.Mean('X', 'X')
    jk = operations.Jackknife('cookie', m)
    df = pd.DataFrame({
        'X': range(6),
        'cookie': [1, 2, 3, 1, 2, 3],
    })
    # Don't use autospec=True. It conflicts with wraps.
    # https://bugs.python.org/issue31807
    with mock.patch.object(
        m, 'compute_through', wraps=m.compute_through) as mock_fn:
      jk.compute_on(df)
      self.assertEqual(1, mock_fn.call_count)
      mock_fn.assert_has_calls([mock.call(df, [])])

  def test_precompute_count(self):
    m = metrics.Count('X')
    jk = operations.Jackknife('cookie', m)
    df = pd.DataFrame({
        'X': range(6),
        'cookie': [1, 2, 3, 1, 2, 3],
    })
    # Don't use autospec=True. It conflicts with wraps.
    # https://bugs.python.org/issue31807
    with mock.patch.object(
        m, 'compute_through', wraps=m.compute_through) as mock_fn:
      jk.compute_on(df)
      self.assertEqual(1, mock_fn.call_count)
      mock_fn.assert_has_calls([mock.call(df, [])])

  def test_precompute_dot(self):
    m = metrics.Dot('X', 'X')
    jk = operations.Jackknife('cookie', m)
    df = pd.DataFrame({
        'X': range(6),
        'cookie': [1, 2, 3, 1, 2, 3],
    })
    # Don't use autospec=True. It conflicts with wraps.
    # https://bugs.python.org/issue31807
    with mock.patch.object(
        m, 'compute_through', wraps=m.compute_through) as mock_fn:
      jk.compute_on(df)
      self.assertEqual(1, mock_fn.call_count)
      mock_fn.assert_has_calls([mock.call(df, [])])

  def test_precompute_dot_normalized(self):
    m = metrics.Dot('X', 'X', True)
    jk = operations.Jackknife('cookie', m)
    df = pd.DataFrame({
        'X': range(6),
        'cookie': [1, 2, 3, 1, 2, 3],
    })
    # Don't use autospec=True. It conflicts with wraps.
    # https://bugs.python.org/issue31807
    with mock.patch.object(
        m, 'compute_through', wraps=m.compute_through) as mock_fn:
      jk.compute_on(df)
      mock_fn.assert_called_once()
      mock_fn.assert_has_calls([mock.call(df, [])])

  def test_internal_caching_with_two_identical_jackknifes(self):
    df = pd.DataFrame({
        'X': range(6),
        'cookie': [1, 2, 3, 1, 2, 3],
    })
    sum_x = metrics.Sum('X')
    jk1 = operations.Jackknife('cookie', sum_x)
    jk2 = operations.Jackknife('cookie', sum_x)
    m = (jk1 - jk2)
    with mock.patch.object(
        sum_x, 'compute_through', wraps=sum_x.compute_through) as mock_fn:
      m.compute_on(df)
      mock_fn.assert_called_once()

  def test_internal_caching_with_two_different_jackknifes(self):
    df = pd.DataFrame({
        'X': range(6),
        'cookie': [1, 2, 3, 1, 2, 3],
        'cookie2': [1, 2, 2, 3, 2, 3],
    })
    sum_x = metrics.Sum('X')
    jk1 = operations.Jackknife('cookie', sum_x)
    jk2 = operations.Jackknife('cookie2', sum_x)
    m = (jk1 - jk2)
    with mock.patch.object(
        sum_x, 'compute_through', wraps=sum_x.compute_through) as mock_fn:
      m.compute_on(df)
      self.assertEqual(2, mock_fn.call_count)

  def test_internal_caching_cleaned_up(self):
    df = pd.DataFrame({
        'X': range(6),
        'cookie': [1, 2, 3, 1, 2, 3],
    })
    sum_x = metrics.Sum('X')
    mean_x = metrics.Mean('X')
    count_x = metrics.Count('X')
    metric = metrics.MetricList((sum_x, mean_x, count_x))
    jk = operations.Jackknife('cookie', metric)
    jk.compute_on(df)
    self.assertEqual(sum_x.cache, {})
    self.assertEqual(mean_x.cache, {})
    self.assertEqual(count_x.cache, {})
    self.assertEqual(metric.cache, {})
    self.assertEqual(jk.cache, {})

    self.assertIsNone(sum_x.cache_key)
    self.assertIsNone(mean_x.cache_key)
    self.assertIsNone(count_x.cache_key)
    self.assertIsNone(metric.cache_key)
    self.assertIsNone(jk.cache_key)

  def test_monkey_patched_compute_slices_recovered(self):
    df = pd.DataFrame({
        'X': range(6),
        'cookie': [1, 2, 3, 1, 2, 3],
    })
    sum_x = metrics.Sum('X')
    mean_x = metrics.Mean('X')
    count_x = metrics.Count('X')
    metric = metrics.MetricList((sum_x, mean_x, count_x))
    jk = operations.Jackknife('cookie', metric)
    jk.compute_on(df)

    sum_x.compute_slices(df)
    self.assertEqual(sum_x.cache, {})
    mean_x.compute_slices(df)
    self.assertEqual(mean_x.cache, {})
    count_x.compute_slices(df)
    self.assertEqual(count_x.cache, {})

  def test_internal_caching_splitby(self):
    df = pd.DataFrame({
        'X': range(12),
        'cookie': [1, 2, 3, 1, 2, 3] * 2,
        'grp': ['A', 'B'] * 6
    })
    sum_x = metrics.Sum('X')
    count_x = sum_x / metrics.Mean('X')
    metric = metrics.MetricList((sum_x, count_x))
    jk = operations.Jackknife('cookie', metric)
    # Don't use autospec=True. It conflicts with wraps.
    # https://bugs.python.org/issue31807
    with mock.patch.object(
        sum_x, 'compute_through', wraps=sum_x.compute_through) as mock_fn:
      jk.compute_on(df, 'grp')
      mock_fn.assert_called_once()
      mock_fn.assert_has_calls([mock.call(df, ['grp'])])

  def test_jackknife_with_count_distinct(self):
    df = pd.DataFrame({
        'X': [1, 2, 2],
        'cookie': [1, 2, 3],
    })
    m = operations.Jackknife('cookie', metrics.Count('X', distinct=True))
    output = m.compute_on(df)
    expected = pd.DataFrame({
        ('count(distinct X)', 'Value'): [2.],
        ('count(distinct X)', 'Jackknife SE'): [2. / 3]
    })
    expected.columns.names = ['Metric', None]
    testing.assert_frame_equal(output, expected)

  def test_jackknife_with_operation(self):
    df = pd.DataFrame({
        'X': range(1, 6),
        'cookie': [1, 1, 2, 3, 4],
        'condition': ['foo', 'foo', 'bar', 'bar', 'bar']
    })
    sum_x = metrics.Sum('X')
    mean_x = metrics.Mean('X')
    count_x = sum_x / mean_x
    metric = metrics.MetricList((sum_x, mean_x, count_x))
    change = operations.AbsoluteChange('condition', 'foo', metric)
    jk_change = operations.Jackknife('cookie', change)
    output = jk_change.compute_on(df, melted=True)
    sum_std = np.std((6, 5, 4), ddof=1) * 2 / np.sqrt(3)
    mean_std = np.std((3, 2.5, 2), ddof=1) * 2 / np.sqrt(3)
    expected = pd.DataFrame({
        'Value': [9, 2.5, 1],
        'Jackknife SE': [sum_std, mean_std, 0],
        'Metric': [
            'sum(X) Absolute Change', 'mean(X) Absolute Change',
            'sum(X) / mean(X) Absolute Change'
        ]
    })
    expected['condition'] = 'bar'
    expected.set_index(['Metric', 'condition'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_jackknife_with_operation_with_multiple_columns_display(self):
    df = pd.DataFrame({
        'X': range(1, 6),
        'cookie': [1, 1, 2, 3, 4],
        'condition': ['foo', 'foo', 'bar', 'bar', 'bar'],
        'grp': ['A', 'A', 'A', 'B', 'B'],
    })
    sum_x = metrics.Sum('X')
    change = operations.AbsoluteChange(['condition', 'grp'], ('foo', 'A'),
                                       sum_x)
    jk_change = operations.Jackknife('cookie', change, 0.9)
    output = jk_change.compute_on(df)
    output.display()

  def test_operation_with_jackknife(self):
    df = pd.DataFrame({
        'X': range(1, 11),
        'cookie': [1, 1, 2, 3, 4] * 2,
        'condition': ['foo', 'foo', 'bar', 'bar', 'bar'] * 2,
        'grp': ['A'] * 6 + ['B'] * 4
    })
    ms = metrics.MetricList(
        [metrics.Sum('X', where='X > 3'),
         metrics.Mean('X', where='X > 5')])
    jk = operations.Jackknife('cookie', ms, where='X > 4')
    m = operations.AbsoluteChange('grp', 'A', jk, where='X > 2')
    output = m.compute_on(df)

    sumx = metrics.Sum('X')
    meanx = metrics.Mean('X')
    jk = operations.Jackknife('cookie')
    ab = operations.AbsoluteChange('grp', 'A')
    expected_sum = ab(jk(sumx)).compute_on(df[df.X > 4])
    expected_mean = ab(jk(meanx)).compute_on(df[df.X > 5])
    expected = pd.concat((expected_sum, expected_mean), 1)
    testing.assert_frame_equal(output, expected)

  def test_cache_key_with_operation(self):
    df = pd.DataFrame({
        'X': range(1, 11),
        'cookie': [1, 1, 2, 3, 4] * 2,
        'condition': ['foo', 'foo', 'bar', 'bar', 'bar'] * 2,
    })
    sum_x = metrics.Sum('X')
    count_x = sum_x / metrics.Mean('X')
    metric = metrics.MetricList((sum_x, count_x))
    change = operations.AbsoluteChange('condition', 'foo', metric)
    jk = operations.Jackknife('cookie', change)
    jk.compute_on(df, cache_key=42)
    self.assertTrue(sum_x.in_cache(42, 'condition'))
    self.assertTrue(count_x.in_cache(42, 'condition'))
    self.assertTrue(metric.in_cache(42, 'condition'))
    self.assertTrue(change.in_cache(42))
    self.assertTrue(jk.in_cache(42))

  def test_internal_caching_with_operation(self):
    df = pd.DataFrame({
        'X': np.arange(0, 6, 0.5),
        'cookie': [1, 2, 3, 1, 2, 3] * 2,
        'grp': list('AABBAB') * 2,
        'condition': [0, 1] * 6
    })
    sum_x = metrics.Sum('X')
    count_x = sum_x / metrics.Mean('X')
    metric = metrics.MetricList((sum_x, count_x))
    pct = operations.PercentChange('condition', 0, metric)
    jk = operations.Jackknife('cookie', pct, 0.9)
    # Don't use autospec=True. It conflicts with wraps.
    # https://bugs.python.org/issue31807
    with mock.patch.object(
        sum_x, 'compute_through', wraps=sum_x.compute_through) as mock_fn:
      jk.compute_on(df)
      mock_fn.assert_called_once()
      mock_fn.assert_has_calls([mock.call(df, ['condition'])])

  def test_monkey_patched_compute_slices_recovered_with_operation(self):
    df = pd.DataFrame({
        'X': np.arange(0, 6, 0.5),
        'cookie': [1, 2, 3, 1, 2, 3] * 2,
        'grp': list('AABBAB') * 2,
        'condition': [0, 1] * 6
    })
    sum_x = metrics.Sum('X')
    mean_x = metrics.Mean('X')
    count_x = metrics.Count('X')
    metric = metrics.MetricList((sum_x, count_x))
    pct = operations.PercentChange('condition', 0, metric)
    jk = operations.Jackknife('cookie', pct)
    jk.compute_on(df)

    sum_x.compute_slices(df)
    self.assertEqual(sum_x.cache, {})
    mean_x.compute_slices(df)
    self.assertEqual(mean_x.cache, {})
    count_x.compute_slices(df)
    self.assertEqual(count_x.cache, {})

  def test_jackknife_with_operation_splitby(self):
    df = pd.DataFrame({
        'X': range(1, 11),
        'cookie': [1, 1, 2, 3, 4] * 2,
        'condition': ['foo', 'foo', 'bar', 'bar', 'bar'] * 2,
        'grp': ['A'] * 5 + ['B'] * 5
    })
    output = self.jk_change.compute_on(df, 'grp')
    expected = []
    for g in ['A', 'B']:
      expected.append(self.jk_change.compute_on(df[df.grp == g]))
    expected = pd.concat(expected, keys=['A', 'B'], names=['grp'])
    testing.assert_frame_equal(output, expected)

  def test_internal_caching_with_operation_splitby(self):
    df = pd.DataFrame({
        'X': np.arange(0, 6, 0.5),
        'cookie': [1, 2, 3, 1, 2, 3] * 2,
        'grp': list('AABBAB') * 2,
        'condition': [0, 1] * 6
    })
    sum_x = metrics.Sum('X')
    count_x = sum_x / metrics.Mean('X')
    metric = metrics.MetricList((sum_x, count_x))
    pct = operations.PercentChange('condition', 0, metric)
    jk = operations.Jackknife('cookie', pct)
    # Don't use autospec=True. It conflicts with wraps.
    # https://bugs.python.org/issue31807
    with mock.patch.object(
        sum_x, 'compute_through', wraps=sum_x.compute_through) as mock_fn:
      jk.compute_on(df, 'grp')
      mock_fn.assert_called_once()
      mock_fn.assert_has_calls([mock.call(df, ['grp', 'condition'])])

  def test_internal_caching_with_operation_cleaned_up(self):
    df = pd.DataFrame({
        'X': range(12),
        'cookie': [1, 2, 3] * 4,
        'grp': list('AABBAB') * 2,
        'condition': [0, 1] * 6
    })
    sum_x = metrics.Sum('X')
    mean_x = metrics.Mean('X')
    count_x = metrics.Count('X')
    metric = metrics.MetricList((sum_x, mean_x, count_x))
    expected = metric.compute_on(df)
    pct = operations.PercentChange('condition', 0, metric)
    jk = operations.Jackknife('cookie', pct)
    jk.compute_on(df, 'grp')
    self.assertEqual(sum_x.cache, {})
    self.assertEqual(mean_x.cache, {})
    self.assertEqual(count_x.cache, {})
    self.assertEqual(metric.cache, {})
    self.assertEqual(pct.cache, {})
    self.assertEqual(jk.cache, {})

    self.assertIsNone(sum_x.cache_key)
    self.assertIsNone(mean_x.cache_key)
    self.assertIsNone(count_x.cache_key)
    self.assertIsNone(metric.cache_key)
    self.assertIsNone(pct.cache_key)
    self.assertIsNone(jk.cache_key)

    # Check monkey patched methods are recovered
    testing.assert_frame_equal(metric.compute_on(df), expected)

  def test_unequal_leaf_index_broacasting(self):
    df = pd.DataFrame({
        'X': range(6),
        'grp': ['A'] * 3 + ['B'] * 3,
        'cookie': [1, 2, 3, 1, 2, 3]
    })
    s = metrics.Sum('X')
    pct = operations.PercentChange('grp', 'A', s)
    m = operations.Jackknife('cookie', s * pct, 0.9)
    output = m.compute_on(df, melted=True)
    output_pt_est = output['Value']
    expected = pct.compute_on(df, melted=True).iloc[:, 0] * df.X.sum()
    expected.index = expected.index.set_levels(
        ['sum(X) * sum(X) Percent Change'], 0)
    testing.assert_series_equal(expected, output_pt_est)
    output.display()

  def test_integration(self):
    df = pd.DataFrame({
        'X': np.arange(0, 3, 0.5),
        'cookie': [1, 2, 3, 1, 2, 3],
        'grp': list('AABBAB')
    })
    change = metrics.Sum('X') | operations.AbsoluteChange('grp', 'A')
    m = change | operations.Jackknife('cookie')
    output = m.compute_on(df, melted=True)
    std = np.std((1, 5, -1), ddof=1) * 2 / np.sqrt(3)

    expected = pd.DataFrame([['sum(X) Absolute Change', 'B', 2.5, std]],
                            columns=['Metric', 'grp', 'Value', 'Jackknife SE'])
    expected.set_index(['Metric', 'grp'], inplace=True)
    testing.assert_frame_equal(output, expected)

  def test_display_simple_metric(self):
    df = pd.DataFrame({
        'X': np.arange(0, 3, 0.5),
        'cookie': [1, 2, 3, 1, 2, 3],
    })
    m = operations.Jackknife('cookie', metrics.Sum('X'), 0.9)
    res = m.compute_on(df)
    output = res.display(return_formatted_df=True)
    expected = pd.DataFrame({
        'sum(X)': [
            '<div class="ci-display-good-change ci-display-cell"><div>'
            '<span class="ci-display-ratio">7.5000</span>'
            '<div class="ci-display-flex-line-break"></div>'
            '<span class="ci-display-ci-range">[4.1283, 10.8717]</span>'
            '</div></div>'
        ]
    })
    expected.columns.name = 'Metric'
    testing.assert_frame_equal(output, expected)

  def test_display_simple_metric_split_by(self):
    df = pd.DataFrame({
        'X': np.arange(0, 3, 0.5),
        'cookie': [1, 2, 3, 1, 2, 3],
        'grp': list('AABBAB')
    })
    m = operations.Jackknife('cookie', metrics.Sum('X'), 0.9)
    res = m.compute_on(df, 'grp')
    output = res.display(return_formatted_df=True)
    expected = pd.DataFrame(
        {
            'Dimensions': [
                '<div><div><span class="ci-display-dimension">A</span></div>'
                '</div>',
                '<div><div><span class="ci-display-dimension">B</span></div>'
                '</div>'
            ],
            'sum(X)': [
                '<div class="ci-display-cell"><div>'
                '<span class="ci-display-ratio">2.5000</span>'
                '<div class="ci-display-flex-line-break"></div>'
                '<span class="ci-display-ci-range">[-5.3922, 10.3922]</span>'
                '</div></div>', '<div class="ci-display-cell"><div>'
                '<span class="ci-display-ratio">5.0000</span>'
                '<div class="ci-display-flex-line-break"></div>'
                '<span class="ci-display-ci-range">[-1.3138, 11.3138]</span>'
                '</div></div>'
            ]
        },)
    expected.columns.name = 'Metric'
    testing.assert_frame_equal(output, expected)

  def test_display_change(self):
    df = pd.DataFrame({
        'X': [1, 100, 2, 100, 3, 100],
        'cookie': [1, 2, 3, 1, 2, 3],
        'grp': ['A', 'B'] * 3
    })
    change = metrics.Sum('X') | operations.PercentChange('grp', 'A')
    m = operations.Jackknife('cookie', change, 0.9)
    res = m.compute_on(df)
    output = res.display(return_formatted_df=True)
    expected = pd.DataFrame(
        {
            'Dimensions': [
                '<div><div><span class="ci-display-experiment-id">A</span>'
                '</div></div>',
                '<div><div><span class="ci-display-experiment-id">B</span>'
                '</div></div>'
            ],
            'sum(X)': [
                '<div class="ci-display-cell">6.0000</div>',
                '<div class="ci-display-good-change ci-display-cell">'
                '<div>300.0000<div class="ci-display-flex-line-break"></div>'
                '<span class="ci-display-ratio">4900.00%</span>'
                '<div class="ci-display-flex-line-break"></div>'
                '<span class="ci-display-ci-range">[357.80, 9442.20] %</span>'
                '</div></div>'
            ]
        },)
    expected.columns.name = 'Metric'
    testing.assert_frame_equal(output, expected)

  def test_display_change_split_by(self):
    df = pd.DataFrame({
        'X': list(range(0, 5)) + list(range(1000, 1004)),
        'cookie': [1, 2, 3] * 3,
        'grp': list('AB') * 4 + ['B'],
        'expr': ['foo'] * 5 + ['bar'] * 4
    })
    change = metrics.Sum('X') | operations.AbsoluteChange('expr', 'foo')
    m = operations.Jackknife('cookie', change, 0.9)
    res = m.compute_on(df, 'grp')
    output = res.display(return_formatted_df=True)
    expected = pd.DataFrame(
        {
            'Dimensions': [
                '<div><div><span class="ci-display-experiment-id">foo</span>'
                '<div class="ci-display-flex-line-break"></div>'
                '<span class="ci-display-dimension">A</span></div></div>',
                '<div><div><span class="ci-display-experiment-id">bar</span>'
                '<div class="ci-display-flex-line-break"></div>'
                '<span class="ci-display-dimension">A</span></div></div>',
                '<div><div><span class="ci-display-experiment-id">foo</span>'
                '<div class="ci-display-flex-line-break"></div>'
                '<span class="ci-display-dimension">B</span></div></div>',
                '<div><div><span class="ci-display-experiment-id">bar</span>'
                '<div class="ci-display-flex-line-break"></div>'
                '<span class="ci-display-dimension">B</span></div></div>'
            ],
            'sum(X)': [
                '<div class="ci-display-cell">6.0000</div>',
                '<div class="ci-display-good-change ci-display-cell">'
                '<div>1001.0000<div class="ci-display-flex-line-break"></div>'
                '<span class="ci-display-ratio">995.0000</span>'
                '<div class="ci-display-flex-line-break"></div>'
                '<span class="ci-display-ci-range">[988.6862, 1001.3138]</span>'
                '</div></div>', '<div class="ci-display-cell">4.0000</div>',
                '<div class="ci-display-cell">'
                '<div>3005.0000<div class="ci-display-flex-line-break">'
                '</div><span class="ci-display-ratio">3001.0000</span>'
                '<div class="ci-display-flex-line-break"></div>'
                '<span class="ci-display-ci-range">[-380.8246, 6382.8246]'
                '</span></div></div>'
            ]
        },)
    expected.columns.name = 'Metric'
    testing.assert_frame_equal(output, expected)


class BootstrapTests(unittest.TestCase):

  n = 100
  x = np.arange(0, 3, 0.5)
  df = pd.DataFrame({'X': x, 'grp': ['A'] * 3 + ['B'] * 3})
  metric = metrics.MetricList((metrics.Sum('X'), metrics.Count('X')))
  bootstrap_no_unit = operations.Bootstrap(None, metric, n)
  bootstrap_unit = operations.Bootstrap('unit', metric, n)

  def test_get_samples(self):
    m = operations.Bootstrap(None, metrics.Sum('X'), 2)
    output = [s[1] for s in m.get_samples(self.df)]
    self.assertLen(output, 2)
    for s in output:
      self.assertLen(s, len(self.df))

  def test_get_samples_splitby(self):
    m = operations.Bootstrap(None, metrics.Sum('X'), 2)
    output = [s[1] for s in m.get_samples(self.df, 'grp')]
    self.assertLen(output, 2)
    expected = self.df.groupby('grp').size()
    for s in output:
      testing.assert_series_equal(s.groupby('grp').size(), expected)

  def test_get_samples_with_unit(self):
    m = operations.Bootstrap('grp', metrics.Sum('X'), 10)
    output = [s[1] for s in m.get_samples(self.df)]
    self.assertLen(output, 10)
    grp_cts = self.df.groupby('grp').size()
    for s in output:
      self.assertEqual([2], (s.groupby('grp').size() / grp_cts).sum())

  def test_get_samples_with_unit_splitby(self):
    df = pd.DataFrame({
        'X': range(10),
        'grp': ['A'] * 2 + ['B'] * 3 + ['C'] * 4 + ['D'],
        'grp2': ['foo'] * 5 + ['bar'] * 5
    })
    m = operations.Bootstrap('grp', metrics.Sum('X'), 10)
    output = [s[1] for s in m.get_samples(df, 'grp2')]
    self.assertLen(output, 10)
    grp_cts = df.groupby(['grp2', 'grp']).size()
    for s in output:
      s = s.groupby(['grp2', 'grp']).size()
      self.assertEqual([2], (s / grp_cts).groupby(['grp2']).sum().unique())

  def test_bootstrap_no_unit(self):
    np.random.seed(42)
    unmelted = self.bootstrap_no_unit.compute_on(self.df)

    np.random.seed(42)
    estimates = []
    for _ in range(self.n):
      buckets_sampled = np.random.choice(range(len(self.x)), size=len(self.x))
      sample = self.df.iloc[buckets_sampled]
      res = metrics.Sum('X').compute_on(sample, return_dataframe=False)
      estimates.append(res)
    std_sumx = np.std(estimates, ddof=1)

    expected = pd.DataFrame(
        [[7.5, std_sumx, 6., 0.]],
        columns=pd.MultiIndex.from_product(
            [['sum(X)', 'count(X)'], ['Value', 'Bootstrap SE']],
            names=['Metric', None]))
    testing.assert_frame_equal(unmelted, expected)

    np.random.seed(42)
    melted = self.bootstrap_no_unit.compute_on(self.df, melted=True)
    expected = pd.DataFrame(
        data={
            'Value': [7.5, 6.],
            'Bootstrap SE': [std_sumx, 0.]
        },
        columns=['Value', 'Bootstrap SE'],
        index=['sum(X)', 'count(X)'])
    expected.index.name = 'Metric'
    testing.assert_frame_equal(melted, expected)

  def test_bootstrap_unit(self):
    df = pd.DataFrame({'X': self.x, 'unit': ['A', 'A', 'B', 'B', 'C', 'C']})
    np.random.seed(42)
    unmelted = self.bootstrap_unit.compute_on(df)

    np.random.seed(42)
    estimates = []
    for _ in range(self.n):
      buckets_sampled = np.random.choice(['A', 'B', 'C'], size=3)
      sample = pd.concat(df[df['unit'] == b] for b in buckets_sampled)
      res = metrics.Sum('X').compute_on(sample, return_dataframe=False)
      estimates.append(res)
    std_sumx = np.std(estimates, ddof=1)

    expected = pd.DataFrame(
        [[7.5, std_sumx, 6., 0.]],
        columns=pd.MultiIndex.from_product(
            [['sum(X)', 'count(X)'], ['Value', 'Bootstrap SE']],
            names=['Metric', None]))
    testing.assert_frame_equal(unmelted, expected)

    np.random.seed(42)
    melted = self.bootstrap_unit.compute_on(df, melted=True)
    expected = pd.DataFrame(
        data={
            'Value': [7.5, 6.],
            'Bootstrap SE': [std_sumx, 0.]
        },
        columns=['Value', 'Bootstrap SE'],
        index=['sum(X)', 'count(X)'])
    expected.index.name = 'Metric'
    testing.assert_frame_equal(melted, expected)

  def test_bootstrap_splitby(self):
    np.random.seed(42)
    unmelted = self.bootstrap_no_unit.compute_on(self.df, 'grp')

    np.random.seed(42)
    expected = []
    grps = ['A', 'B']
    for g in grps:
      expected.append(
          self.bootstrap_no_unit.compute_on(self.df[self.df.grp == g]))
    expected = pd.concat(expected, keys=grps, names=['grp'])
    expected = expected.droplevel(-1)  # empty level
    # The order of grps is not derterministic, so we don't check exaclty.
    testing.assert_frame_equal(unmelted, expected, check_less_precise=1)

    np.random.seed(42)
    melted = self.bootstrap_no_unit.compute_on(self.df, 'grp', melted=True)
    testing.assert_frame_equal(
        melted, utils.melt(expected), check_less_precise=1)

  def test_bootstrap_splitby_multiple(self):
    df = pd.concat([self.df, self.df], keys=['foo', 'bar'], names=['grp0'])
    output = operations.Bootstrap(None, self.metric, self.n,
                                  0.9).compute_on(df, ['grp0', 'grp'])
    self.assertEqual(output.index.names, ['grp0', 'grp'])
    output.display()  # Check display() runs.

  def test_bootstrap_where(self):
    df = pd.DataFrame({'X': range(1, 7), 'grp': ['B'] * 3 + ['A'] * 3})
    metric = operations.Bootstrap(
        None, metrics.Sum('X'), self.n, where='grp == "A"')
    metric_no_filter = operations.Bootstrap(None, metrics.Sum('X'), self.n)
    np.random.seed(42)
    output = metric.compute_on(df)
    np.random.seed(42)
    expected = metric_no_filter.compute_on(df[df.grp == 'A'])
    testing.assert_frame_equal(output, expected)

  def test_confidence(self):
    np.random.seed(42)
    melted = operations.Bootstrap(None, self.metric, self.n, 0.9).compute_on(
        self.df, melted=True)
    np.random.seed(42)
    expected = self.bootstrap_no_unit.compute_on(self.df, melted=True)
    multiplier = stats.t.ppf((1 + .9) / 2, self.n - 1)
    expected['Bootstrap CI-lower'] = expected[
        'Value'] - multiplier * expected['Bootstrap SE']
    expected['Bootstrap CI-upper'] = expected[
        'Value'] + multiplier * expected['Bootstrap SE']
    expected.drop('Bootstrap SE', 1, inplace=True)
    testing.assert_frame_equal(melted, expected)
    melted.display()  # Check display() runs.

    np.random.seed(42)
    unmelted = operations.Bootstrap(None, self.metric, self.n,
                                    0.9).compute_on(self.df)
    expected = pd.DataFrame(
        [
            list(melted.loc['sum(X)'].values) +
            list(melted.loc['count(X)'].values)
        ],
        columns=pd.MultiIndex.from_product(
            [['sum(X)', 'count(X)'],
             ['Value', 'Bootstrap CI-lower', 'Bootstrap CI-upper']],
            names=['Metric', None]))
    testing.assert_frame_equal(unmelted, expected)
    unmelted.display()  # Check display() runs.

  def test_cache_key(self):
    df = pd.DataFrame({'X': range(6), 'Y': range(100, 106)})
    sum_x = metrics.Sum('X')
    sum_y = metrics.Sum('Y')
    ratio = sum_x / sum_y
    metric = metrics.MetricList((sum_x, ratio))
    bootstrap = operations.Bootstrap(None, metric, self.n)
    bootstrap.compute_on(df, cache_key='foo')
    self.assertTrue(sum_x.in_cache('foo'))
    self.assertTrue(sum_y.in_cache('foo'))
    self.assertTrue(ratio.in_cache('foo'))
    self.assertTrue(metric.in_cache('foo'))
    self.assertTrue(bootstrap.in_cache('foo'))

  def test_internal_caching_cleaned_up(self):
    df = pd.DataFrame({'X': range(6), 'Y': range(100, 106)})
    sum_x = metrics.Sum('X')
    sum_y = metrics.Sum('Y')
    ratio = sum_x / sum_y
    metric = metrics.MetricList((sum_x, ratio))
    bootstrap = operations.Bootstrap(None, metric, self.n)
    bootstrap.compute_on(df)
    self.assertEqual(sum_x.cache, {})
    self.assertEqual(sum_y.cache, {})
    self.assertEqual(ratio.cache, {})
    self.assertEqual(metric.cache, {})
    self.assertEqual(bootstrap.cache, {})

    self.assertIsNone(sum_x.cache_key)
    self.assertIsNone(sum_y.cache_key)
    self.assertIsNone(ratio.cache_key)
    self.assertIsNone(metric.cache_key)
    self.assertIsNone(bootstrap.cache_key)

  def test_integration(self):
    change = metrics.Sum('X') | operations.AbsoluteChange('grp', 'A')
    m = change | operations.Bootstrap(None, n_replicates=self.n)
    np.random.seed(42)
    output = m.compute_on(self.df, melted=True)
    np.random.seed(42)
    estimates = []
    for _ in range(self.n):
      buckets_sampled = np.random.choice(range(len(self.x)), size=len(self.x))
      sample = self.df.iloc[buckets_sampled]
      try:  # Some sampls might not have any grp 'A' rows.
        res = change.compute_on(sample)
        estimates.append(res.iloc[0, 0])
      except:  # pylint: disable=bare-except
        continue
    std = np.std(estimates, ddof=1)

    expected = pd.DataFrame([['sum(X) Absolute Change', 'B', 4.5, std]],
                            columns=['Metric', 'grp', 'Value', 'Bootstrap SE'])
    expected.set_index(['Metric', 'grp'], inplace=True)
    testing.assert_frame_equal(output, expected)


@parameterized.named_parameters(
    ('Distribution', operations.Distribution('condition')),
    ('CumulativeDistribution', operations.CumulativeDistribution('condition')),
    ('PercentChange', operations.PercentChange('condition', 0)),
    ('AbsoluteChange', operations.AbsoluteChange('condition', 0)),
    ('MH', operations.MH('condition', 0, 'cookie')),
    ('Jackknife', operations.Jackknife('cookie')))
class FilterTest(parameterized.TestCase):
  np.random.seed(42)
  n = 40
  df = pd.DataFrame({
      'clicks': np.random.choice(range(20), n),
      'impressions': np.random.choice(range(20), n),
      'cookie': np.random.choice(range(5), n),
      'condition': np.random.choice(range(2), n),
  })

  def test_parent(self, op):
    m = copy.deepcopy(op)
    m.where = 'clicks > 5'
    ctr = metrics.Ratio('impressions', 'clicks')
    output = m(ctr).compute_on(self.df)
    expected = op(ctr).compute_on(self.df[self.df.clicks > 5])
    testing.assert_frame_equal(output, expected)

  def test_child(self, op):
    metric = op(metrics.Ratio('impressions', 'clicks', where='clicks > 5'))
    output = metric.compute_on(self.df)
    expected = op(metrics.Ratio('impressions', 'clicks')).compute_on(
        self.df[self.df.clicks > 5])
    testing.assert_frame_equal(output, expected)

  def test_children(self, op):
    m1 = metrics.Ratio('clicks', 'impressions', where='clicks > 5')
    m2 = metrics.Ratio('impressions', 'clicks', where='clicks > 10')
    metric = op(metrics.MetricList((m1, m2)))
    output = metric.compute_on(self.df)
    expected1 = op(m1).compute_on(self.df)
    expected2 = op(m2).compute_on(self.df)
    expected = pd.concat((expected1, expected2), 1)
    testing.assert_frame_equal(output, expected)

  def test_metriclist_child(self, op):
    m = metrics.Ratio('impressions', 'clicks', where='clicks > 5')
    m = metrics.MetricList([m], where='impressions > 4')
    metric = op(m)
    output = metric.compute_on(self.df)
    expected = op(metrics.Ratio('impressions', 'clicks')).compute_on(
        self.df[(self.df.clicks > 5) & (self.df.impressions > 4)])
    testing.assert_frame_equal(output, expected)

  def test_metriclist_children(self, op):
    m1 = metrics.Ratio('clicks', 'impressions', where='clicks > 5')
    m2 = metrics.Ratio('impressions', 'clicks', where='clicks > 10')
    m = metrics.Ratio('impressions', 'clicks', where='clicks > 5')
    m = metrics.MetricList([m1, m2], where='impressions > 4')
    metric = op(m)
    output = metric.compute_on(self.df)
    expected1 = op(m1).compute_on(self.df[self.df.impressions > 4])
    expected2 = op(m2).compute_on(self.df[self.df.impressions > 4])
    expected = pd.concat((expected1, expected2), 1)
    testing.assert_frame_equal(output, expected)


if __name__ == '__main__':
  unittest.main()
