ALTER TABLE voice_asr_acceptance ADD COLUMN outcome_status INT NULL;
ALTER TABLE voice_asr_acceptance ADD COLUMN outcome_cipher BLOB NULL;
ALTER TABLE voice_asr_acceptance ADD COLUMN outcome_iv VARBINARY(12) NULL;
ALTER TABLE voice_asr_acceptance ADD COLUMN completed_at VARCHAR(32) NULL;
ALTER TABLE voice_asr_acceptance ADD COLUMN result_expires_at VARCHAR(32) NULL;
CREATE INDEX ix_voice_asr_result_expiry ON voice_asr_acceptance(result_expires_at);

CREATE TABLE voice_asr_audit (
 id VARCHAR(36) NOT NULL PRIMARY KEY,
 user_id BIGINT NOT NULL,
 request_id VARCHAR(36) NOT NULL,
 outcome_status INT NOT NULL,
 created_at VARCHAR(32) NOT NULL,
 expires_at VARCHAR(32) NOT NULL,
 INDEX ix_voice_asr_audit_expiry(expires_at)
);
