# Copyright (c) 2021 vesoft inc. All rights reserved.
#
# This source code is licensed under Apache 2.0 License,
# attached with Common Clause Condition 1.0, found in the LICENSES directory.
Feature: Zone balance

  Scenario: balance cross zone
    Given a nebulacluster with 1 graphd and 1 metad and 6 storaged

  Scenario: balance in a zone
    Given a nebulacluster with 1 graphd and 1 metad and 6 storaged
  
  Scenario: balance if zone is no host
    Given a nebulacluster with 1 graphd and 1 metad and 6 storaged
  
  Scenario: balance remove
    Given a nebulacluster with 1 graphd and 1 metad and 6 storage
  
  Scenario: balance remove then re-balance
    Given a nebulacluster with 1 graphd and 1 metad and 6 storage