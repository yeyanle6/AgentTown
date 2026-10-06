CREATE TABLE `community_sources` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`payload` text NOT NULL,
	`sha256` text NOT NULL,
	`retrieved_at` text NOT NULL
);
