ALTER TABLE users
ADD COLUMN display_name TEXT
CHECK (display_name IS NULL OR length(display_name) BETWEEN 1 AND 200);

ALTER TABLE users
ADD COLUMN job_title TEXT
CHECK (job_title IS NULL OR length(job_title) <= 200);

ALTER TABLE users
ADD COLUMN company TEXT
CHECK (company IS NULL OR length(company) <= 200);

ALTER TABLE users
ADD COLUMN time_zone TEXT
CHECK (time_zone IS NULL OR length(time_zone) <= 100);
