CREATE INDEX ix_p_oracle  ON printings(oracle_id, pref);
CREATE INDEX ix_p_name    ON printings(name_norm);
CREATE INDEX ix_p_set     ON printings(set_code, collector_sort);
CREATE INDEX ix_p_cmc     ON printings(cmc);
CREATE INDEX ix_p_release ON printings(released_at);
