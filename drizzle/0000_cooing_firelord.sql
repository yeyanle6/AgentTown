CREATE TABLE `community_artifacts` (
	`id` text PRIMARY KEY NOT NULL,
	`task_id` text NOT NULL,
	`payload` text NOT NULL,
	`sha256` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `community_commands` (
	`id` text PRIMARY KEY NOT NULL,
	`kind` text NOT NULL,
	`payload` text NOT NULL,
	`status` text DEFAULT 'pending' NOT NULL,
	`result` text,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_community_commands_status` ON `community_commands` (`status`,`created_at`);--> statement-breakpoint
CREATE TABLE `community_snapshots` (
	`id` text PRIMARY KEY NOT NULL,
	`run_id` text NOT NULL,
	`payload` text NOT NULL,
	`updated_at` text NOT NULL
);
